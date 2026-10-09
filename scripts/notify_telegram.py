#!/usr/bin/env python3
"""docs/data/brief.txt 또는 docs/data/alerts.txt 를 텔레그램 봇으로 보낸다.

환경 변수 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 가 없으면 보내지 않고 그 사실만 출력한다(실패로 처리하지 않는다).
--mode digest  : brief.txt 를 보낸다 (주간 브리핑)
--mode alerts  : alerts.txt 가 비어 있지 않을 때만 보낸다 (조건 알림)
텔레그램 메시지 한 건은 4096자까지이므로 긴 본문은 줄 단위로 나눠 보낸다.
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LIMIT = 3800


def send(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(f"telegram error: {payload}")


def chunks(text):
    buf = ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > LIMIT and buf:
            yield buf
            buf = ""
        buf += line + "\n"
    if buf.strip():
        yield buf


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["digest", "alerts"], required=True)
    ap.add_argument("--docs", default=os.path.join(ROOT, "docs"))
    args = ap.parse_args()
    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    path = os.path.join(args.docs, "data", "brief.txt" if args.mode == "digest" else "alerts.txt")
    text = open(path, encoding="utf-8").read().strip() if os.path.exists(path) else ""
    if not text:
        print(f"{args.mode}: nothing to send")
        return
    if not token or not chat_id:
        print(f"{args.mode}: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set; would have sent {len(text)} chars")
        return
    n = 0
    for part in chunks(text):
        send(token, chat_id, part)
        n += 1
    print(f"{args.mode}: sent {n} message(s), {len(text)} chars")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # 알림 실패가 빌드 자체를 실패로 만들지 않도록 종료 코드는 0, 메시지는 stderr
        print(f"telegram send failed: {e}", file=sys.stderr)
