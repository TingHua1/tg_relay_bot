#!/usr/bin/env python3
"""Minimal Telegram DM relay: strangers -> admin, admin replies in-thread -> user."""
from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path

TOKEN = os.environ.get("TG_RELAY_BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = os.environ.get("TG_RELAY_ADMIN_CHAT_ID", "").strip()
DB_PATH = Path(os.environ.get("TG_RELAY_DB", "/var/lib/tg-relay-bot/relay.db"))
API = f"https://api.telegram.org/bot{TOKEN}"


def api(method: str, **params):
    data = {k: v for k, v in params.items() if v is not None}
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(f"{API}/{method}", data=body)
    with urllib.request.urlopen(req, timeout=45) as r:
        result = json.load(r)
    if not result.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {result}")
    return result["result"]


def init_db():
    DB_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS relay_map (admin_message_id INTEGER PRIMARY KEY, user_chat_id INTEGER NOT NULL, created_at INTEGER NOT NULL)")
    con.execute("CREATE TABLE IF NOT EXISTS contacts (user_chat_id INTEGER PRIMARY KEY, label TEXT NOT NULL, first_seen_at INTEGER NOT NULL, last_seen_at INTEGER NOT NULL)")
    columns = {row[1] for row in con.execute("PRAGMA table_info(contacts)")}
    if "last_admin_message_id" not in columns:
        con.execute("ALTER TABLE contacts ADD COLUMN last_admin_message_id INTEGER")
    con.execute("CREATE TABLE IF NOT EXISTS relay_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    # Preserve historical contacts from the old reply map so the first message
    # after this upgrade is correctly marked as returning, not new.
    con.execute(
        "INSERT OR IGNORE INTO contacts (user_chat_id, label, first_seen_at, last_seen_at) "
        "SELECT user_chat_id, '历史联系人（ID ' || user_chat_id || '）', MIN(created_at), MAX(created_at) "
        "FROM relay_map GROUP BY user_chat_id"
    )
    con.execute(
        "UPDATE contacts SET last_admin_message_id = ("
        "SELECT MAX(admin_message_id) FROM relay_map WHERE relay_map.user_chat_id = contacts.user_chat_id"
        ") WHERE last_admin_message_id IS NULL"
    )
    con.commit()
    return con


def user_label(user: dict) -> str:
    name = " ".join(x for x in [user.get("first_name"), user.get("last_name")] if x)
    username = f"@{user['username']}" if user.get("username") else "无用户名"
    return f"{name or '未命名'}｜{username}｜ID {user.get('id', '?')}"


def forward_to_admin(con, message: dict):
    user = message.get("from") or {}
    chat_id = message["chat"]["id"]
    label = user_label(user)
    now = int(time.time())
    known_row = con.execute("SELECT last_admin_message_id FROM contacts WHERE user_chat_id = ?", (chat_id,)).fetchone()
    known = known_row is not None
    previous_message_id = known_row[0] if known_row else None
    active = con.execute("SELECT value FROM relay_state WHERE key = 'active_contact' ").fetchone()
    switched = active is None or active[0] != str(chat_id)

    con.execute(
        "INSERT INTO contacts (user_chat_id, label, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_chat_id) DO UPDATE SET label = excluded.label, last_seen_at = excluded.last_seen_at",
        (chat_id, label, now, now),
    )
    con.execute(
        "INSERT INTO relay_state (key, value) VALUES ('active_contact', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(chat_id),),
    )

    if switched:
        prefix = "🆕 新联系人" if not known else "📨 已联系过的联系人"
        header_params = {"chat_id": ADMIN_CHAT_ID, "text": f"{prefix}：{label}\n回复本消息或下方转发消息即可回复对方。"}
        if known and previous_message_id:
            header_params["reply_to_message_id"] = previous_message_id
        header = api("sendMessage", **header_params)
        con.execute("INSERT OR REPLACE INTO relay_map VALUES (?, ?, ?)", (header["message_id"], chat_id, now))

    forwarded = api("forwardMessage", chat_id=ADMIN_CHAT_ID, from_chat_id=chat_id, message_id=message["message_id"])
    con.execute("INSERT OR REPLACE INTO relay_map VALUES (?, ?, ?)", (forwarded["message_id"], chat_id, now))
    con.execute("UPDATE contacts SET last_admin_message_id = ? WHERE user_chat_id = ?", (forwarded["message_id"], chat_id))
    con.commit()


def reply_to_user(con, message: dict):
    reply = message.get("reply_to_message") or {}
    row = con.execute("SELECT user_chat_id FROM relay_map WHERE admin_message_id = ?", (reply.get("message_id"),)).fetchone()
    if not row:
        return False
    text = message.get("text") or message.get("caption")
    if text:
        api("sendMessage", chat_id=row[0], text=text)
    else:
        # Preserve admin media/documents without requiring file downloads.
        api("copyMessage", chat_id=row[0], from_chat_id=message["chat"]["id"], message_id=message["message_id"])
    return True


def main():
    if not TOKEN:
        raise SystemExit("TG_RELAY_BOT_TOKEN is empty; configure /etc/tg-relay-bot.env before starting")
    if not ADMIN_CHAT_ID:
        raise SystemExit("TG_RELAY_ADMIN_CHAT_ID is empty; configure the administrator numeric Telegram chat ID before starting")
    con = init_db()
    offset = 0
    api("deleteWebhook", drop_pending_updates=False)
    me = api("getMe")
    print(f"relay bot @{me.get('username', '?')} started", flush=True)
    while True:
        try:
            updates = api("getUpdates", offset=offset, timeout=35, allowed_updates=json.dumps(["message"]))
            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message") or {}
                chat = message.get("chat") or {}
                chat_id = str(chat.get("id", ""))
                if chat_id == ADMIN_CHAT_ID:
                    if message.get("text", "").strip() in {"/start", "/help"}:
                        api("sendMessage", chat_id=chat_id, text="回复某条转发消息即可联系对应用户。")
                    else:
                        reply_to_user(con, message)
                elif chat.get("type") == "private":
                    if message.get("text", "").strip() == "/start":
                        api("sendMessage", chat_id=chat_id, text="消息已转交管理员，请等待回复。")
                    else:
                        forward_to_admin(con, message)
        except Exception as exc:
            print(f"relay error: {type(exc).__name__}: {exc}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
