"""Per-browser plus-address inboxes and the Flex Bucks guest-invite emails sent to them.

Each browser gets a random tag (kept in a cookie). The student invites
cafwrapped+<tag>@wrapped.drew.place as a Flex Bucks guest; the Cloudflare Email
Worker forwards the raw invite to /api/inbound, which parses it here and stores
the guest login under that tag so the same browser can re-run later without
inviting again. Tags are random, so knowing a student ID reveals nothing.
"""
import json
import os
import re
import secrets
from datetime import datetime
from email import message_from_bytes, policy

from bs4 import BeautifulSoup

DOMAIN = "wrapped.drew.place"
CREDENTIALS_DIR = os.path.join(os.path.dirname(__file__), "data", "credentials")

_TAG_RE = re.compile(r"^[0-9a-f]{16}$")
_RECIPIENT_RE = re.compile(r"cafwrapped\+([0-9a-f]{16})@", re.IGNORECASE)
_USERNAME_RE = re.compile(r"(?:username|login)\s*:\s*(\S+)", re.IGNORECASE)
_PASSWORD_RE = re.compile(r"password\s*:\s*(\S+)", re.IGNORECASE)
_NAME_RE = re.compile(r"\bname\s*:\s*([A-Za-z][A-Za-z .'-]*?)\s*(?:https?://|\n|$)", re.IGNORECASE)


def new_tag() -> str:
    return secrets.token_hex(8)


def is_tag(value: str | None) -> bool:
    return bool(value) and bool(_TAG_RE.match(value))


def plus_address(tag: str) -> str:
    return f"cafwrapped+{tag}@{DOMAIN}"


def tag_from_recipient(recipient: str) -> str | None:
    m = _RECIPIENT_RE.search(recipient or "")
    return m.group(1).lower() if m else None


def _body_text(raw: bytes) -> str:
    msg = message_from_bytes(raw, policy=policy.default)
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    text = part.get_content()
    if part.get_content_subtype() == "html":
        text = BeautifulSoup(text, "lxml").get_text("\n")
    return text


def parse_invite(raw: bytes, tag: str) -> dict | None:
    """Return {username, password, name} from a guest-invite email, or None.

    Atrium guests log in with their guest email, so the username falls back to
    this inbox's address when the email doesn't spell it out.
    """
    text = _body_text(raw)
    password = _PASSWORD_RE.search(text)
    if not password:
        return None
    username = _USERNAME_RE.search(text)
    name = _NAME_RE.search(text)
    return {
        "username": username.group(1) if username else plus_address(tag),
        "password": password.group(1),
        "name": name.group(1).strip() if name else None,
    }


def _cred_path(tag: str) -> str:
    if not is_tag(tag):
        raise ValueError("invalid inbox tag")
    return os.path.join(CREDENTIALS_DIR, f"{tag}.json")


def save_credentials(tag: str, invite: dict) -> None:
    os.makedirs(CREDENTIALS_DIR, exist_ok=True)
    path = _cred_path(tag)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump({**invite, "received_at": datetime.now().isoformat(timespec="seconds")}, f)
    os.replace(path + ".tmp", path)


def load_credentials(tag: str) -> dict | None:
    try:
        with open(_cred_path(tag), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def delete_credentials(tag: str) -> None:
    try:
        os.remove(_cred_path(tag))
    except FileNotFoundError:
        pass
