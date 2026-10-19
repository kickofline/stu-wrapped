"""Usage analytics: an append-only event log in data/analytics.db (SQLite).

No third-party scripts and no IP addresses. A visitor is a salted hash of the browser's
inbox cookie, so one browser's landing, signup, run and shares line up without storing
the tag itself. Signup and run events carry the student's name; tools/report.py reads
the log (plus saved results, archive and invites) and prints the report.

Recording never raises: a broken analytics write must not break a page.
"""
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import traceback
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DB_FILE = os.path.join(DATA_DIR, "analytics.db")
SALT_FILE = os.path.join(DATA_DIR, "analytics_salt")

# Events the browser may send to /api/event, with the properties each may carry.
CLIENT_EVENTS = {
    "email_copied": set(),
    "flex_link_opened": set(),
    "slide_reached": {"index", "total", "slide"},
    "share_slide": {"slide", "method"},
    "share_link": {"method"},
    "compare_shared": {"method"},
}
SHARE_METHODS = {"share", "download", "copy", "dismissed"}

_BOT = re.compile(r"bot|crawl|spider|slurp|preview|facebookexternalhit|whatsapp|telegram|"
                  r"curl|wget|python-requests|httpx|headless|lighthouse", re.IGNORECASE)
# Link-preview fetchers of the og:image, most specific first. iMessage (and other Apple
# apps) identify as "facebookexternalhit/1.1 Facebot Twitterbot/1.0".
_PREVIEW_APPS = (
    ("facebot twitterbot", "iMessage"), ("facebookexternalhit", "Facebook/Instagram/Messenger"),
    ("twitterbot", "X"), ("discordbot", "Discord"), ("slackbot", "Slack"), ("whatsapp", "WhatsApp"),
    ("telegrambot", "Telegram"), ("snapchat", "Snapchat"), ("linkedinbot", "LinkedIn"),
    ("googlebot", "Google"), ("applebot", "Apple"),
)

_lock = threading.Lock()
_salt: str | None = None
_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,          -- local time, ISO seconds
    name TEXT NOT NULL,
    visitor TEXT,              -- salted hash of the inbox cookie
    student TEXT,              -- student_key (same as results/stats)
    share_id TEXT,
    props TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS events_name_ts ON events (name, ts);
CREATE INDEX IF NOT EXISTS events_student ON events (student);
"""


def connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    con = sqlite3.connect(DB_FILE, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(_SCHEMA)
    return con


def _get_salt() -> str:
    global _salt
    if _salt is None:
        with _lock:
            if _salt is None:
                try:
                    with open(SALT_FILE, encoding="utf-8") as f:
                        _salt = f.read().strip()
                except FileNotFoundError:
                    os.makedirs(os.path.dirname(SALT_FILE), exist_ok=True)
                    _salt = secrets.token_hex(16)
                    with open(SALT_FILE, "w", encoding="utf-8") as f:
                        f.write(_salt)
    return _salt


def visitor_id(tag: str | None) -> str | None:
    if not tag:
        return None
    return hashlib.sha256(f"{_get_salt()}:{tag}".encode()).hexdigest()[:16]


def is_bot(user_agent: str | None) -> bool:
    return not user_agent or bool(_BOT.search(user_agent))


def preview_app(user_agent: str | None) -> str:
    ua = (user_agent or "").lower()
    for needle, app in _PREVIEW_APPS:
        if needle in ua:
            return app
    return "browser/other"


def record(event: str, *, visitor: str | None = None, student: str | None = None,
           share_id: str | None = None, ts: str | None = None, **props) -> None:
    """Append one event. Extra keyword arguments (a student's name, a share method...)
    go into its props; None values are dropped."""
    try:
        con = connect()
        try:
            with con:
                con.execute(
                    "INSERT INTO events (ts, name, visitor, student, share_id, props) VALUES (?, ?, ?, ?, ?, ?)",
                    (ts or datetime.now().isoformat(timespec="seconds"), event, visitor, student, share_id,
                     json.dumps({k: v for k, v in props.items() if v is not None})),
                )
        finally:
            con.close()
    except Exception:
        print(f"[analytics] could not record {event}:\n{traceback.format_exc()}", flush=True)
