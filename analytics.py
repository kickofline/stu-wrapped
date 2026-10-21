"""Usage analytics and the canonical student identity database.

No third-party scripts and no IP addresses. A visitor is a salted hash of the browser's
inbox cookie, so one browser's landing, signup, run and shares line up without storing
the tag itself. ``events.student`` is the five-digit Atrium customer account ID; the
``students`` table stores that ID, the derived OBU email and the current display name.
tools/report.py combines this database with saved results, archives and invites.

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
    "open_browser_clicked": set(),
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
# In-app browsers (a link tapped inside Instagram, Snapchat...), most specific first.
_INAPP_APPS = (
    ("instagram", "Instagram"), ("fban/", "Facebook"), ("fbav/", "Facebook"), ("fb_iab", "Facebook"),
    ("snapchat", "Snapchat"), ("musical_ly", "TikTok"), ("bytedancewebview", "TikTok"),
    ("yikyak", "Yik Yak"), ("yik yak", "Yik Yak"),
)

_lock = threading.Lock()
_salt: str | None = None
_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,          -- local time, ISO seconds
    name TEXT NOT NULL,
    visitor TEXT,              -- salted hash of the inbox cookie
    student TEXT,              -- five-digit Atrium customer account ID
    share_id TEXT,
    props TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS events_name_ts ON events (name, ts);
CREATE INDEX IF NOT EXISTS events_student ON events (student);
CREATE TABLE IF NOT EXISTS students (
    student_id TEXT PRIMARY KEY
        CHECK (length(student_id) = 5 AND student_id NOT GLOB '*[^0-9]*'),
    email TEXT NOT NULL UNIQUE,
    name TEXT,
    updated_at TEXT NOT NULL
);
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


def save_student(student_id: str, email: str, name: str | None) -> None:
    """Insert or refresh one verified Atrium identity.

    Unlike event recording, this is required application state: callers see database
    errors instead of silently producing a Wrapped with no canonical identity.
    """
    if not re.fullmatch(r"\d{5}", student_id):
        raise ValueError("invalid Atrium student ID")
    if not re.fullmatch(r"[a-z]{3}\d{5}@obu\.edu", email):
        raise ValueError("invalid OBU email")
    con = connect()
    try:
        with con:
            con.execute(
                """INSERT INTO students (student_id, email, name, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(student_id) DO UPDATE SET
                     email = excluded.email,
                     name = excluded.name,
                     updated_at = excluded.updated_at""",
                (student_id, email, name, datetime.now().isoformat(timespec="seconds")),
            )
    finally:
        con.close()


def identify_visitor(visitor: str | None, student_id: str) -> None:
    """Attach pre-scrape events from this browser to its newly verified student ID."""
    if not visitor:
        return
    con = connect()
    try:
        with con:
            con.execute(
                "UPDATE events SET student = ? WHERE visitor = ? AND student IS NULL",
                (student_id, visitor),
            )
    finally:
        con.close()


def is_bot(user_agent: str | None) -> bool:
    return not user_agent or bool(_BOT.search(user_agent))


def preview_app(user_agent: str | None) -> str:
    ua = (user_agent or "").lower()
    for needle, app in _PREVIEW_APPS:
        if needle in ua:
            return app
    return "browser/other"


def inapp_browser(user_agent: str | None) -> str | None:
    """The app whose built-in browser this is ("Instagram"; "app" for an unnamed iOS or
    Android web view), or None for a real browser. iOS web views drop Safari's
    "Safari/" token, which every iOS browser (Chrome, Firefox, Edge) keeps."""
    ua = (user_agent or "").lower()
    for needle, app in _INAPP_APPS:
        if needle in ua:
            return app
    if re.search(r"iphone|ipad|ipod", ua) and "applewebkit" in ua and "safari/" not in ua:
        return "app"
    if "android" in ua and "; wv)" in ua:
        return "app"
    return None


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
