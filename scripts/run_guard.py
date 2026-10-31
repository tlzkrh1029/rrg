#!/usr/bin/env python3
"""워크플로의 첫 작업: 이번 실행이 아침 빌드인지 저녁 실행인지 정하고, 돌릴지 거를지 GITHUB_OUTPUT 에 적는다.

시작은 보통 Claude 예약 작업이 보내는 workflow_dispatch(정시)이고, GitHub 예약 실행(schedule)은 2~4시간 늦게 시작하곤 해서 예비로 둔다.
아침 빌드와 아침 예비 실행은 미국 동부 시각(20:05 · 20:10 ET)으로 정해 두어 서머타임 전환과 상관없이 장후 거래가 끝난 뒤에 돈다.
  mode    dispatch 는 입력값(없으면 morning), schedule 은 EVENING_CRON 이면 evening, 나머지는 morning
  run     아침: 뉴욕 정규장 시간(평일 09:30~16:00 ET)이면 거른다(장중 가격을 종가로 쓰지 않게).
                예비 실행(schedule)은 12시간 안에 아침 빌드 커밋('terminal <날짜>')이 이미 있으면 거른다.
          저녁: 뉴욕 장전 시간(평일 04:00~09:30 ET)이 아니면 거른다(장전 가격이 없거나 다음 날의 장후를 받게 된다).
                예비 실행은 6시간 안에 저녁 커밋('evening <날짜>')이 이미 있으면 거른다.
  digest  아침 빌드이고, 한국 시각으로 토요일이거나 입력 digest 가 true 이면 true

환경 변수: EVENT(github.event_name), SCHED(github.event.schedule), IN_MODE, IN_DIGEST, BRANCH(기본 브랜치), GITHUB_OUTPUT.
--now 2026-11-02T20:05:00-05:00 처럼 시각을 주면 그 시각으로 판단한다(시험용, 커밋 확인은 건너뛴다).
"""

import argparse
import datetime as dt
import os
import subprocess
import sys

try:
    from zoneinfo import ZoneInfo
    NY, KST = ZoneInfo("America/New_York"), ZoneInfo("Asia/Seoul")
except Exception:  # noqa: BLE001  (tzdata 가 없는 환경: 서머타임을 무시한 근사)
    NY, KST = dt.timezone(dt.timedelta(hours=-4)), dt.timezone(dt.timedelta(hours=9))

EVENING_CRON = "5 9 * * 1-5"


def recent_commit(branch, grep, hours):
    """기본 브랜치의 최근 커밋 가운데 메시지가 grep 으로 시작하고 hours 시간 안에 만든 것의 시각(없으면 None)."""
    try:
        subprocess.run(["git", "fetch", "--quiet", "--depth=50", "origin", branch], check=True, timeout=120)
        out = subprocess.run(["git", "log", "FETCH_HEAD", f"--since={hours} hours ago", f"--grep=^{grep}", "--format=%cI %s", "-1"],
                             check=True, capture_output=True, text=True, timeout=60).stdout.strip()
        return out or None
    except Exception as e:  # noqa: BLE001  (확인하지 못하면 거르지 않는다)
        print(f"::warning::could not check recent commits ({e}); not skipping")
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", default=None)
    args = ap.parse_args()
    now = dt.datetime.fromisoformat(args.now) if args.now else dt.datetime.now(dt.timezone.utc)
    ny, kst = now.astimezone(NY), now.astimezone(KST)
    event, sched = os.environ.get("EVENT", ""), os.environ.get("SCHED", "")
    if event == "schedule":
        mode = "evening" if sched == EVENING_CRON else "morning"
    else:
        mode = (os.environ.get("IN_MODE") or "morning").strip() or "morning"
    branch = os.environ.get("BRANCH") or "main"
    mins = ny.hour * 60 + ny.minute
    weekday = ny.weekday() < 5
    run, why = True, ""
    if mode == "morning":
        if weekday and 9 * 60 + 30 <= mins < 16 * 60:
            run, why = False, f"New York regular session ({ny:%a %H:%M} ET): a morning build now would take intraday prices as closes"
        elif event == "schedule" and not args.now:
            hit = recent_commit(branch, "terminal 20", 12)
            if hit:
                run, why = False, f"backup run: today's morning build is already in ({hit})"
    else:
        if not (weekday and 4 * 60 <= mins < 9 * 60 + 30):
            run, why = False, f"not in the New York pre-market ({ny:%a %H:%M} ET, 04:00~09:30 on weekdays)"
        elif event == "schedule" and not args.now:
            hit = recent_commit(branch, "evening 20", 6)
            if hit:
                run, why = False, f"backup run: an evening build is already in ({hit})"
    digest = mode == "morning" and (kst.weekday() == 5 or (os.environ.get("IN_DIGEST") or "").lower() == "true")
    print(f"{event or 'manual'} · {mode} · {kst:%a %H:%M} KST / {ny:%a %H:%M} ET · run={run}" + (f" · skip: {why}" if why else "") + f" · digest={digest}")
    if not run:
        print(f"::notice::skipped: {why}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"mode={mode}\nrun={'true' if run else 'false'}\ndigest={'true' if digest else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
