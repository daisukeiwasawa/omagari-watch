#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ticket Pass「2026 大曲の花火」駐車場チケット 空き監視スクリプト

・対象ページを取得し、各駐車場が「完売」から変わったらメールで通知します。
・外部ライブラリ不要（Python 3.8以上の標準ライブラリのみ）。

必要な環境変数:
  SMTP_USER            送信元メールアドレス（例: you@gmail.com）
  SMTP_PASS            SMTPパスワード（Gmailなら「アプリパスワード」）
  MAIL_TO              通知先アドレス（省略時は SMTP_USER と同じ）
  SMTP_HOST            既定 smtp.gmail.com
  SMTP_PORT            既定 465（SSL）
  NOTIFY_EVERY_TIME    1 にすると、空きが続く限り毎回通知（既定は状態変化時のみ）
"""

import html
import json
import os
import re
import smtplib
import ssl
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

URL = "https://ticket-pass.com/events/view/omagari-hanabi_summer"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
JST = timezone(timedelta(hours=9))

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")
MAIL_TO = os.environ.get("MAIL_TO") or SMTP_USER
NOTIFY_EVERY_TIME = os.environ.get("NOTIFY_EVERY_TIME", "0") == "1"

SOLD_OUT = "完売ZZZ"
AVAILABLE = "販売中(要確認)"


def fetch_page() -> str:
    req = urllib.request.Request(
        URL,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
            "Accept-Language": "ja,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read().decode("utf-8", errors="replace")


def parse_tickets(page: str):
    """チケット一覧を [(チケットID, 駐車場名, 状態), ...] で返す"""
    # チケット詳細リンクを目印テキストに置き換えてからタグを除去する
    marked = re.sub(
        r'(?i)<a[^>]*?/tickets/view/(\d+)[^>]*?>', r'@@TICKET:\1@@<a>', page
    )
    marked = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", marked)
    text = re.sub(r"<[^>]+>", "\n", marked)
    text = html.unescape(text)
    # タグ除去で大量に生じる空行・空白を圧縮する（これをしないと直前の記述に届かない）
    text = "\n".join(ln for ln in (l.strip() for l in text.split("\n")) if ln)

    parts = re.split(r"@@TICKET:(\d+)@@", text)
    tickets = []
    for i in range(1, len(parts), 2):
        ticket_id = parts[i]
        block = parts[i - 1][-400:]  # そのチケットの直前の記述だけを見る

        name_matches = re.findall(r"駐車場\s*[0-9０-９]+番[^\n]*", block)
        name = name_matches[-1].strip() if name_matches else f"チケットID {ticket_id}"

        status = SOLD_OUT if SOLD_OUT in block else AVAILABLE
        tickets.append((ticket_id, name, status))
    return tickets


def load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"[warn] 状態ファイルを保存できませんでした: {e}", file=sys.stderr)


def send_mail(subject: str, body: str) -> None:
    if not (SMTP_USER and SMTP_PASS and MAIL_TO):
        print("[error] SMTP_USER / SMTP_PASS / MAIL_TO が未設定です", file=sys.stderr)
        sys.exit(1)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = MAIL_TO
    msg.set_content(body)

    context = ssl.create_default_context()
    if SMTP_PORT == 587:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
            s.starttls(context=context)
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
    else:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=30) as s:
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)


def main() -> int:
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST")

    try:
        page = fetch_page()
    except Exception as e:
        print(f"[{now}] 取得失敗: {e}", file=sys.stderr)
        return 1

    tickets = parse_tickets(page)
    if not tickets:
        print(f"[{now}] チケットを1件も検出できませんでした（ページ構成の変更の可能性）",
              file=sys.stderr)
        return 1

    print(f"[{now}] {len(tickets)}件を確認")
    for _, name, status in tickets:
        print(f"  {status:>10}  {name}")

    prev = load_state()
    current = {tid: status for tid, _, status in tickets}

    newly_open = []
    for tid, name, status in tickets:
        if status != AVAILABLE:
            continue
        if NOTIFY_EVERY_TIME or prev.get(tid) != AVAILABLE:
            newly_open.append((tid, name))

    if newly_open:
        lines = [
            "大曲の花火 駐車場チケットに空きが出た可能性があります。",
            f"検知時刻: {now}",
            "",
        ]
        for tid, name in newly_open:
            lines.append(f"■ {name}")
            lines.append(f"  https://ticket-pass.com/tickets/view/{tid}")
            lines.append("")
        lines.append(f"一覧ページ: {URL}")
        lines.append("")
        lines.append("※先着順・お一人様1枚です。表示の反映遅れもあるため必ずサイトでご確認ください。")

        subject = f"【空き検知】大曲の花火 駐車場 {len(newly_open)}件 ({now[5:16]})"
        send_mail(subject, "\n".join(lines))
        print(f"[{now}] 通知メールを送信しました（{len(newly_open)}件）")

    save_state(current)
    return 0


if __name__ == "__main__":
    sys.exit(main())
