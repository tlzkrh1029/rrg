#!/usr/bin/env python3
"""탭 줄 아래 '주요 일정' 한 줄에 넣을, 지난 밤 발표된 경제 일정을 받는다.

하루 지도(비공개 저장소의 한 브랜치)가 매일 아침 화면 사진에서 읽어 둔 기록(폴더 안의 날짜.json) 가운데 가장 새 날짜 파일을
GitHub API 로 한 개만 받아, calendar 칸의 항목마다 다음 여덟 값만 output/key-calendar.json 에 옮긴다.
  시각(한국 시각 HH:MM) · 나라 · 이름 · 중요도(별 0~3) · 발표값 · 예상과 견준 방향(up/down/same) · 예상 · 이전
메모(note)와 그 밖의 칸(속보, 뉴스, 실적 줄 등)은 읽은 뒤 버린다. 이 저장소의 실행 기록은 누구나 볼 수 있으므로
로그에는 날짜와 건수만 적고, 토큰은 요청 머리에만 넣는다.

환경 변수
  DMAP_TOKEN     그 저장소의 Contents 읽기 권한만 가진 토큰(Secrets). 없으면 아무것도 하지 않고 이전 파일을 그대로 둔다.
  DMAP_REPO      owner/name
  DMAP_REF       브랜치
  DMAP_SCREENS   날짜.json 이 있는 폴더

로컬 확인: python terminal/fetch_key_calendar.py --file 날짜.json [--out 경로]

종료 코드: 0 = 받았거나 토큰이 없어 건너뜀, 2 = 받지 못해 이전 파일을 그대로 둠
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "output", "key-calendar.json")
KST = dt.timezone(dt.timedelta(hours=9))
DATE_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")
TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
CTRL = re.compile(r"[\x00-\x1f\x7f]")
DIRS = {"up", "down", "same"}
MAX_ITEMS = 40


def clean(v, n):
    """문자열 한 칸: 제어 문자를 지우고 앞뒤 공백을 정리한 뒤 n 자에서 자른다. 문자열이 아니면 빈 값."""
    if not isinstance(v, str):
        return ""
    return CTRL.sub(" ", v).strip()[:n]


def pick(rec):
    """화면 기록 하나에서 일정 항목만 [시각, 나라, 이름, 별, 발표값, 방향, 예상, 이전] 으로 꺼낸다."""
    out = []
    for c in rec.get("calendar") or []:
        if not isinstance(c, dict):
            continue
        name = clean(c.get("name"), 60)
        if not name:
            continue
        tm = clean(c.get("time"), 5)
        stars = c.get("stars")
        stars = stars if isinstance(stars, int) and 0 <= stars <= 3 else 0
        d = clean(c.get("dir"), 5)
        out.append([tm if TIME_RE.match(tm) else "", clean(c.get("country"), 12), name, stars,
                    clean(c.get("actual"), 24), d if d in DIRS else "", clean(c.get("forecast"), 24), clean(c.get("previous"), 24)])
        if len(out) >= MAX_ITEMS:
            break
    return out


def api_get(url, token, raw=False):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "rrg-build",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def fetch_latest(token, repo, ref, folder):
    """폴더 목록에서 오늘(한국 시각) 이전의 가장 새 날짜 파일을 골라 받는다. (날짜, 기록) 을 돌려준다."""
    q = urllib.parse.quote
    base = f"https://api.github.com/repos/{repo}/contents/{q(folder.strip('/'))}"
    listing = json.loads(api_get(f"{base}?ref={q(ref)}", token))
    today = dt.datetime.now(KST).date().isoformat()
    dates = sorted(m.group(1) for f in listing if isinstance(f, dict) and f.get("type") == "file"
                   for m in [DATE_FILE.match(f.get("name") or "")] if m and m.group(1) <= today)
    if not dates:
        raise ValueError("no dated file in the folder")
    day = dates[-1]
    rec = json.loads(api_get(f"{base}/{day}.json?ref={q(ref)}", token, raw=True))
    return day, rec


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", help="API 대신 이 화면 기록 파일을 읽는다(로컬 확인용)")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    if args.file:
        rec = json.load(open(args.file, encoding="utf-8"))
        m = DATE_FILE.match(os.path.basename(args.file))
        day = str(rec.get("date") or (m.group(1) if m else ""))
    else:
        token = os.environ.get("DMAP_TOKEN", "").strip()
        repo, ref, folder = (os.environ.get(k, "").strip() for k in ("DMAP_REPO", "DMAP_REF", "DMAP_SCREENS"))
        if not token:
            print("key calendar: DMAP_TOKEN is not set; keeping the previous file")
            return 0
        if not (repo and ref and folder):
            print("key calendar: DMAP_REPO, DMAP_REF or DMAP_SCREENS is missing; keeping the previous file")
            return 2
        try:
            day, rec = fetch_latest(token, repo, ref, folder)
        except urllib.error.HTTPError as e:
            print(f"key calendar: GitHub API answered {e.code}; keeping the previous file")
            return 2
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
            print(f"key calendar: could not read the record ({type(e).__name__}); keeping the previous file")
            return 2
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) or not isinstance(rec, dict):
        print("key calendar: the record has no valid date; keeping the previous file")
        return 2
    items = pick(rec)
    try:
        old = json.load(open(args.out, encoding="utf-8"))
    except (OSError, ValueError):
        old = None
    if old and old.get("date") == day and old.get("items") == items:
        print(f"key calendar {day}: {len(items)} items, unchanged")
        return 0
    if old and str(old.get("date") or "") > day:
        print(f"key calendar: the record ({day}) is older than the kept file ({old.get('date')}); keeping it")
        return 0
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    tmp = args.out + ".tmp"
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(tmp, "w", encoding="utf-8") as f:   # 항목 하나를 한 줄에 적어 커밋 차이를 읽기 쉽게 한다
        f.write('{\n "date": %s,\n "fetched_at": %s,\n "items": [\n%s\n ]\n}\n' % (
            json.dumps(day), json.dumps(stamp), ",\n".join("  " + json.dumps(it, ensure_ascii=False) for it in items)))
    os.replace(tmp, args.out)
    print(f"key calendar {day}: {len(items)} items ({sum(1 for x in items if x[4])} with released values)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
