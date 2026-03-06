import hashlib
import imaplib
import email as email_lib
import os
import re
import time
from datetime import datetime
from typing import Callable

GMAIL_USER = "drewdettmer@gmail.com"
GMAIL_IMAP = "imap.gmail.com"
EMAIL_POLL_INTERVAL = 5   # seconds between IMAP checks
EMAIL_TIMEOUT = 300       # 5 minutes before giving up


def make_plus_address(student_id: str) -> str:
    """Stable plus address derived from the user's OBU student ID (same ID → same address)."""
    h = hashlib.sha256(student_id.strip().encode()).hexdigest()[:8]
    return f"cafwrapped+{h}@drew.place"


def _get_text_body(msg) -> str:
    """Extract plain-text body from an email.message.Message."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if ct == "text/plain" and "attachment" not in cd:
                charset = part.get_content_charset() or "utf-8"
                try:
                    return part.get_payload(decode=True).decode(charset, errors="replace")
                except Exception:
                    return part.get_payload(decode=True).decode("utf-8", errors="replace")
    else:
        charset = msg.get_content_charset() or "utf-8"
        try:
            return msg.get_payload(decode=True).decode(charset, errors="replace")
        except Exception:
            return ""


def extract_credentials_from_email(body: str) -> tuple[str, str] | None:
    """
    Try multiple patterns to find username + password in an Atrium credential email.
    Returns (username, password) or None.
    """
    patterns = [
        r"(?:username|login)\s*:\s*(\S+).*?password\s*:\s*(\S+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, body, re.IGNORECASE | re.DOTALL)
        if m:
            username = m.group(1).strip()
            password = m.group(2).strip()
            if username and password:
                return username, password
    return None


def poll_for_credential_email(
    plus_address: str,
    after_timestamp: datetime,
    progress_callback: Callable[[int, str], None],
) -> tuple[str, str]:
    """
    Polls Gmail IMAP for a credential email addressed to plus_address.
    Calls progress_callback(step, message) on each poll cycle.
    Returns (username, password) on success.
    Raises TimeoutError after EMAIL_TIMEOUT seconds.
    """
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not app_password:
        raise RuntimeError("GMAIL_APP_PASSWORD environment variable is not set.")

    since_date = after_timestamp.strftime("%d-%b-%Y")
    start_time = time.time()
    poll_count = 0

    while True:
        elapsed = int(time.time() - start_time)
        if elapsed >= EMAIL_TIMEOUT:
            raise TimeoutError(
                f"Couldn't connect to your Flex Bucks account after {EMAIL_TIMEOUT // 60} minutes. "
                "Make sure you added the correct email in step 2 and try again."
            )

        mins, secs = divmod(elapsed, 60)
        time_str = f"{mins}m {secs:02d}s" if mins else f"{secs}s"
        step = 2 if poll_count < 5 else 3
        progress_callback(step, f"Waiting for Flex Bucks access... ({time_str} elapsed)")

        try:
            creds = _check_inbox_once(app_password, plus_address, since_date)
            if creds:
                return creds
        except Exception as exc:
            progress_callback(step, f"IMAP check failed ({exc}), retrying...")

        poll_count += 1
        time.sleep(EMAIL_POLL_INTERVAL)


def _check_inbox_once(app_password: str, plus_addr: str, since_date: str) -> tuple[str, str] | None:
    """
    Opens a fresh IMAP connection, searches for mail to plus_addr since since_date,
    tries to parse credentials, and closes.
    Returns (username, password) or None.
    """
    with imaplib.IMAP4_SSL(GMAIL_IMAP) as imap:
        imap.login(GMAIL_USER, app_password)

        for folder in ("INBOX", "[Gmail]/Spam"):
            status, _ = imap.select(folder)
            if status != "OK":
                continue

            _, data = imap.search(None, f'(TO "{plus_addr}" SINCE {since_date})')
            msg_ids = data[0].split() if data[0] else []

            for msg_id in msg_ids:
                _, msg_data = imap.fetch(msg_id, "(RFC822)")
                raw = msg_data[0][1] if msg_data and msg_data[0] else None
                if not raw:
                    continue

                msg = email_lib.message_from_bytes(raw)

                to_header = msg.get("To", "").lower()
                if plus_addr.lower() not in to_header:
                    continue

                body = _get_text_body(msg)
                creds = extract_credentials_from_email(body)
                if creds:
                    return creds

    return None
