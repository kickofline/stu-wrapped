"""Per-browser inbox addresses and the Flex Bucks guest-invite emails sent to them.

Each browser gets a random tag (kept in a cookie). The student invites
<tag>@stu.exposed as a Flex Bucks guest; the Cloudflare Email Worker forwards
the raw invite to /api/inbound, which parses it here and stores the guest login
under that tag so the same browser can re-run later without inviting again.
Tags are 6 characters from a-z and 0-9 (2.2 billion of them): short enough to
type, and a new tag is never one that already has a saved login.
"""
import json
import os
import re
import secrets
from datetime import datetime
from email import message_from_bytes, policy
from email.utils import parseaddr

from bs4 import BeautifulSoup

DOMAIN = "stu.exposed"
CREDENTIALS_DIR = os.path.join(os.path.dirname(__file__), "data", "credentials")

TAG_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
_TAG_RE = re.compile(r"^[a-z0-9]{6}$")
_RECIPIENT_RE = re.compile(r"(?:^|[<\s])([a-z0-9]{6})@", re.IGNORECASE)
# Atrium's guest invites come from this address only; its DMARC policy is quarantine.
INVITE_SENDER = "noreply@atriumcampus.com"
_USERNAME_RE = re.compile(r"(?:username|login)\s*:\s*(\S+)", re.IGNORECASE)
_PASSWORD_RE = re.compile(r"password\s*:\s*(\S+)", re.IGNORECASE)
_NAME_RE = re.compile(r"\bname\s*:\s*([A-Za-z][A-Za-z .'-]*?)\s*(?:https?://|\n|$)", re.IGNORECASE)


def new_tag() -> str:
    return "".join(secrets.choice(TAG_ALPHABET) for _ in range(6))


def is_tag(value: str | None) -> bool:
    return bool(value) and bool(_TAG_RE.fullmatch(value))


def inbox_address(tag: str) -> str:
    return f"{tag}@{DOMAIN}"


def tag_from_recipient(recipient: str) -> str | None:
    m = _RECIPIENT_RE.search(recipient or "")
    return m.group(1).lower() if m else None


def is_atrium_invite(raw: bytes) -> bool:
    """Anyone can mail <tag>@stu.exposed; only Atrium's own invite may set a saved login."""
    msg = message_from_bytes(raw, policy=policy.default)
    sender = parseaddr(str(msg.get("From", "")))[1].lower()
    auth = " ".join(str(h) for h in msg.get_all("Authentication-Results") or []).lower()
    return sender == INVITE_SENDER and "dmarc=fail" not in auth


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
        "username": username.group(1) if username else inbox_address(tag),
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
    saved = {**invite, "received_at": invite.get("received_at")
             or datetime.now().isoformat(timespec="seconds")}
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(saved, f)
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
