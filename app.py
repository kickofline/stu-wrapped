import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import traceback
from collections import defaultdict
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from flask import (Flask, Response, abort, flash, jsonify, redirect,
                   render_template, request, send_file, url_for)

load_dotenv()

import analysis
import analytics
import og_image
from insights import NOT_SPOTS, flex_paid, parse_when, spot_name, time_insights
from social import (campus_activity, campus_percentiles, campus_wrapped, dining_badges,
                    dining_streaks, friend_match, nuggets_eaten, roast_facts, signature_order,
                    summary_highlights, tiger_express)
from jobs import (claim, claim_waiting, cleanup_old_jobs, fail, finish, get_job,
                  job_for, update_progress)
from mailbox import (delete_credentials, inbox_address, is_atrium_invite, is_tag,
                     load_credentials, new_tag, parse_invite, save_credentials,
                     tag_from_recipient)
from name_map import expand_name
from scraper import LoginFailed, LoginLimited, NoAccounts, fetch_activity, weekly_meal_plans

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)  # signs flash messages only
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SESSION_COOKIE_SECURE", "1") != "0"
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # raw invite emails

DATA_DIR       = os.path.join(os.path.dirname(__file__), "data")
STATS_FILE     = os.path.join(DATA_DIR, "stats.json")
RAW_ITEMS_FILE = os.path.join(DATA_DIR, "raw_items.json")
RESULTS_DIR    = os.path.join(DATA_DIR, "results")
OG_DIR         = os.path.join(DATA_DIR, "og")  # rendered link-preview images, one per share link
ARCHIVE_DIR    = os.path.join(DATA_DIR, "archive")  # every scrape, raw + parsed, kept forever

INBOX_COOKIE = "inbox"
MINE_COOKIE = "mine"  # share id of this browser's own latest Wrapped
VS_COOKIE = "vs"      # share id of the friend whose Wrapped sent this browser here
_SHARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{12}$")
_stats_lock = threading.Lock()

SITE_NAME = "OBU Stu Wrapped"
SITE_DESCRIPTION = ("Your Ouachita Baptist University dining semester, wrapped: top CFA orders, "
                    "Dr. Jack's lattes, Caf swipes and Flex Bucks spending. Free for OBU students.")


@app.context_processor
def _site_meta():
    return {"site_name": SITE_NAME, "site_description": SITE_DESCRIPTION}


# Pages that show one visitor's inbox address, Wrapped or status: never cache them.
_PRIVATE_PREFIXES = ("/wrapped/", "/results/", "/compare/", "/status/", "/wait/", "/help", "/api/")


@app.after_request
def _security_headers(resp: Response) -> Response:
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
    resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    _refresh_cookies(resp)
    if request.path == "/" or request.path.startswith(_PRIVATE_PREFIXES):
        resp.headers["Cache-Control"] = "private, no-store"
    return resp


def _refresh_cookies(resp: Response) -> None:
    """Slide the 400-day expiry forward on every page visit, so a browser that keeps
    coming back never loses its saved login or its own Wrapped link."""
    if resp.mimetype != "text/html":
        return
    already_set = {header.split("=", 1)[0] for header in resp.headers.getlist("Set-Cookie")}
    for name in (INBOX_COOKIE, MINE_COOKIE):
        value = request.cookies.get(name)
        if value and name not in already_set:
            _set_cookie(resp, name, value)


def _log(*args):
    print(*args, flush=True)

def _notify_signup(name: str | None) -> None:
    """Publish one best-effort notification for each named Atrium invite."""
    url = os.environ.get("NTFY_URL", "").strip()
    display_name = " ".join((name or "").split())
    if not url or not display_name:
        return
    try:
        response = requests.post(url, data=f"signup: {display_name}".encode(), timeout=3)
        response.raise_for_status()
    except requests.RequestException as exc:
        _log(f"[ntfy] signup notification failed: {type(exc).__name__}")


def _aggregate_items(transactions: list) -> list[dict]:
    """Aggregate per-item stats, sorted by qty desc.

    Uses DeepSeek's cleaned items (add-ons folded in, menu names) when the run
    has them, otherwise the raw register lines with name_map expansions."""
    stats: dict = defaultdict(lambda: {"qty": 0, "total_spend": 0.0, "unit_price": None})
    for tx in transactions:
        if "clean_items" in tx:
            for item in tx["clean_items"]:
                s = stats[item["item"]]
                s["qty"] += item["qty"]
                s["total_spend"] += item["total"]
                if s["unit_price"] is None:
                    s["unit_price"] = round(item["total"] / item["qty"], 2)
            continue
        for item in tx["items"]:
            if not item.get("unit_price"):  # skip $0 / free / modifier rows
                continue
            name = expand_name(item["name"])
            stats[name]["qty"] += item["qty"]
            stats[name]["total_spend"] += item.get("subtotal") or 0.0
            if stats[name]["unit_price"] is None and item.get("unit_price") is not None:
                stats[name]["unit_price"] = item["unit_price"]
    sorted_items = sorted(stats.items(), key=lambda x: (-x[1]["qty"], x[0]))
    return [
        {
            "rank":        i + 1,
            "name":        name,
            "qty":         s["qty"],
            "total_spend": round(s["total_spend"], 2),
            "unit_price":  s["unit_price"],
        }
        for i, (name, s) in enumerate(sorted_items)
    ]


def _read_stats() -> dict:
    """Campus-wide comparison stats: {"students": {student_key: latest run summary}}.
    Keyed per student so re-running replaces that student's numbers instead of
    counting them again."""
    try:
        with open(STATS_FILE, encoding="utf-8") as f:
            stats = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        stats = {}
    stats.setdefault("students", {})
    return stats


_stats_cache: dict = {"mtime": None, "stats": {"students": {}}}


def _load_stats() -> dict:
    """Read-only view of the stats, re-parsed only when the file changes (every page
    view needs it). Never modify it; _record_run writes from its own fresh copy."""
    try:
        mtime = os.stat(STATS_FILE).st_mtime_ns
    except FileNotFoundError:
        return {"students": {}}
    if _stats_cache["mtime"] != mtime:
        _stats_cache.update(mtime=mtime, stats=_read_stats())
    return _stats_cache["stats"]


def _save_stats(stats: dict) -> None:
    os.makedirs(os.path.dirname(STATS_FILE), exist_ok=True)
    with open(STATS_FILE + ".tmp", "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)
    os.replace(STATS_FILE + ".tmp", STATS_FILE)


def _student_key(invite: dict, tag: str) -> str:
    """Stable, non-identifying key for one student: the account holder's name from
    the invite (same across browsers and re-invites), else the inbox tag."""
    name = " ".join((invite.get("name") or "").lower().split())
    return hashlib.sha256((name or f"inbox:{tag}").encode()).hexdigest()[:16]


def _result_meta(share_id: str | None) -> dict | None:
    """A saved Wrapped's metadata, or None for a malformed or unknown share id."""
    if not share_id or not _SHARE_ID_RE.fullmatch(share_id):
        return None
    try:
        with open(os.path.join(RESULTS_DIR, f"{share_id}.json"), encoding="utf-8") as f:
            return json.load(f)["metadata"]
    except (OSError, ValueError, KeyError):
        return None


def _same_student(a: dict, b: dict) -> bool:
    """Same student behind two runs: student keys when both have one, otherwise the
    normalized names (runs saved before keys were stored)."""
    if a.get("student_key") and b.get("student_key"):
        return a["student_key"] == b["student_key"]
    name_a = " ".join((a.get("name") or "").casefold().split())
    return bool(name_a) and name_a == " ".join((b.get("name") or "").casefold().split())


def _first_name(meta: dict) -> str:
    parts = (meta.get("name") or "").split()
    return parts[0] if parts else "your friend"


# Browsers cap cookie lifetimes at 400 days (Chrome/Edge enforce it; RFC 6265bis).
COOKIE_MAX_AGE = 400 * 24 * 3600


def _set_cookie(resp: Response, name: str, value: str, max_age: int = COOKIE_MAX_AGE) -> None:
    resp.set_cookie(name, value, max_age=max_age, httponly=True, samesite="Lax",
                    secure=request.is_secure or request.headers.get("X-Forwarded-Proto") == "https")


def _new_inbox_tag() -> str:
    """A fresh inbox tag that no saved login already uses."""
    while True:
        tag = new_tag()
        if load_credentials(tag) is None:
            return tag


def _record_raw_items(transactions: list) -> None:
    """Persist every unique raw item name seen across all runs to raw_items.json."""
    try:
        with open(RAW_ITEMS_FILE, encoding="utf-8") as f:
            known: list = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        known = []
    known_set = set(known)
    for tx in transactions:
        for item in tx["items"]:
            name = item.get("name", "").strip()
            if name:
                known_set.add(name)
    os.makedirs(os.path.dirname(RAW_ITEMS_FILE), exist_ok=True)
    with open(RAW_ITEMS_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(known_set, key=str.lower), f, indent=2)


def _leaderboard_purchases(transactions: list) -> list:
    """Purchases that count on the Campus Leaderboard and in rankings: Tiger Express (the
    Caf's grab-and-go counter) isn't a dining spot, so its Flex purchases are left out."""
    return [t for t in transactions if spot_name(t["location"]) not in NOT_SPOTS]


def _student_summary(transactions: list, swipes: list) -> dict:
    transactions = _leaderboard_purchases(transactions)
    items = _aggregate_items(transactions)
    spots = defaultdict(int)
    for transaction in transactions:
        spots[spot_name(transaction["location"])] += 1
    return {
        "total_qty": sum(item["qty"] for item in items),
        "total_spend": round(sum(flex_paid(t) for t in transactions), 2),
        "unique_items": len(items),
        "top_item": items[0]["name"] if items else None,
        "visits": len(transactions),
        "caf_swipes": len(swipes),
        "nuggets": nuggets_eaten(transactions),
        "items": {item["name"]: item["qty"] for item in items},
        "spots": dict(spots),
    }


def _flex_short(transactions: list, meta: dict, swipes: list) -> bool | None:
    """Whether this student's Flex runs out (or already ran out) before finals end;
    None when there's no Flex plan to judge."""
    plan = _flex_plan(transactions, meta, swipes)
    return None if plan is None else bool(plan.get("runs_out") or plan.get("ran_out"))


def _record_run(transactions: list, swipes: list, student_key: str, metadata: dict) -> None:
    """Store this student's latest run in the campus-wide comparison stats."""
    summary = _student_summary(transactions, swipes)
    summary.update(campus_activity(_leaderboard_purchases(transactions), swipes,
                                   date.fromisoformat(metadata["startdate"]).year))
    summary["flex_short"] = _flex_short(transactions, metadata, swipes)
    summary["updated"] = datetime.now().isoformat(timespec="seconds")
    with _stats_lock:
        _record_raw_items(transactions)
        stats = _read_stats()
        stats["students"][student_key] = summary
        _save_stats(stats)


def _flex_campus(students: list) -> int | None:
    """Percent of students on pace to run out of Flex before finals, once at least three
    students have a Flex plan to judge (so no single student's plan shows through)."""
    judged = [s["flex_short"] for s in students if s.get("flex_short") is not None]
    if len(judged) < 3:
        return None
    return round(sum(judged) / len(judged) * 100)


def _semester_dates() -> tuple[str, str, str]:
    today = date.today()
    if today.month <= 6:
        start = date(today.year, 1, 1)
        label = f"Spring {today.year}"
    else:
        start = date(today.year, 7, 1)
        label = f"Fall {today.year}"
    return start.isoformat(), today.isoformat(), label


# Last day of finals (OBU 2026-27 residential catalog: Fall Dec 14-17, Spring May 3-7).
# Check the new catalog each year.
SEMESTER_END = {"Fall": (12, 17), "Spring": (5, 7)}


def _flex_plan(transactions: list, meta: dict, swipes: list = ()) -> dict | None:
    """Flex left, what it takes to use it up by the end of finals, and where the
    current pace lands. A student already out of Flex gets when it ran out, how long it
    lasted and how many Caf swipes they've used since instead. None when the run has no
    Flex balance, the semester is over, or there's no Flex money to talk about."""
    flex = [v for k, v in (meta.get("balances") or {}).items() if "flex" in k.lower()]
    if not flex:
        return None
    balance = round(sum(flex), 2)
    start, today = date.fromisoformat(meta["startdate"]), date.fromisoformat(meta["enddate"])
    month, day = SEMESTER_END["Spring" if start.month <= 6 else "Fall"]
    end = date(start.year, month, day)
    days_left = (end - today).days
    if days_left <= 0:
        return None
    dated = sorted(((when, tx) for tx in transactions if (when := parse_when(tx["date"], start.year))),
                   key=lambda pair: pair[0])

    if balance < 0.01:
        emptied = next(((when, tx) for when, tx in dated if (tx.get("balance_after") or 0) < 0.01), None)
        if emptied is None:
            return None  # no Flex money this semester at all
        out_at, _ = emptied
        first_at, first = dated[0]
        return {
            "balance": 0.0, "end": f"{end:%B} {end.day}", "days_left": days_left,
            "ran_out": f"{out_at:%B} {out_at.day}", "ran_out_short": f"{out_at:%b} {out_at.day}",
            "started_with": round((first.get("balance_after") or 0) + flex_paid(first), 2),
            "lasted_days": (out_at.date() - first_at.date()).days + 1,
            "swipes_since": sum(1 for s in swipes if (w := parse_when(s.get("date", ""), start.year)) and w > out_at),
        }

    plan = {
        "balance": balance,
        "end": f"{end:%B} {end.day}",
        "days_left": days_left,
        "per_day": round(balance / days_left, 2),
        "per_week": round(balance / days_left * 7, 2),
        "pace_per_week": None, "projected_left": None, "runs_out": None, "ran_out": None,
    }
    if dated:
        spent = sum(flex_paid(tx) for tx in transactions)
        pace = spent / max(1, (today - dated[0][0].date()).days + 1)  # dollars per day so far
        plan["pace_per_week"] = round(pace * 7, 2)
        projected = balance - pace * days_left
        if projected >= 0:
            plan["projected_left"] = round(projected, 2)
        else:
            out = today + timedelta(days=int(balance / pace))
            plan["runs_out"] = f"{out:%B} {out.day}"
    return plan


def _meal_block(meta: dict) -> dict | None:
    """Swipes left on a block meal plan ("Basic Meals": a fixed number for the semester).
    Weekly plans ("weekly_meal_plans": the statement shows Atrium refilling them) only hold
    this week's swipes, so their balance isn't a count worth showing."""
    weekly = meta.get("weekly_meal_plans") or []
    for name, left in (meta.get("balances") or {}).items():
        if "meal" in name.lower() and name not in weekly:
            return {"name": name, "left": int(left)}
    return None


# ── Results (persisted so share links outlive restarts) ─────────────────────

def _save_result(transactions: list, swipes: list, metadata: dict, ai: dict | None) -> str:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    share_id = secrets.token_urlsafe(9)
    with open(os.path.join(RESULTS_DIR, f"{share_id}.json"), "w", encoding="utf-8") as f:
        json.dump({"transactions": transactions, "swipes": swipes, "metadata": metadata, "analysis": ai}, f)
    return share_id


def _load_result(share_id: str) -> dict:
    if not _SHARE_ID_RE.fullmatch(share_id):
        abort(404)
    try:
        with open(os.path.join(RESULTS_DIR, f"{share_id}.json"), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        abort(404)


# ── Jobs ─────────────────────────────────────────────────────────────────────

# Each run logs in to Atrium and calls the model; cap how many run at once.
_SCRAPE_SLOTS = threading.BoundedSemaphore(int(os.environ.get("SCRAPE_CONCURRENCY", "6")))


def _start_job(job_id: str, tag: str, invite: dict) -> None:
    threading.Thread(target=_run_job, args=(job_id, tag, invite), daemon=True).start()


def _run_job(job_id: str, tag: str, invite: dict) -> None:
    """Run one Wrapped in a free slot, and never leave its job stuck on "running"."""
    if not _SCRAPE_SLOTS.acquire(blocking=False):
        update_progress(job_id, 1, "Lots of people are wrapping right now. You're in line…")
        _SCRAPE_SLOTS.acquire()
    try:
        _wrap(job_id, tag, invite)
    except Exception:
        _log(f"[job {job_id}] failed:\n{traceback.format_exc()}")
        analytics.record("wrap_failed", visitor=analytics.visitor_id(tag),
                         student=_student_key(invite, tag), reason="crash")
        fail(job_id, "Something broke while wrapping your semester. Tap Start over to try again.")
    finally:
        _SCRAPE_SLOTS.release()


def _wrap(job_id: str, tag: str, invite: dict) -> None:
    startdate, enddate, label = _semester_dates()
    student = _student_key(invite, tag)

    def failed(reason: str, message: str) -> None:
        analytics.record("wrap_failed", visitor=analytics.visitor_id(tag), student=student,
                         name=invite.get("name"), reason=reason)
        fail(job_id, message)

    try:
        transactions, swipes, balances, pages = fetch_activity(
            invite["username"], invite["password"], startdate, enddate,
            progress=lambda step, msg: update_progress(job_id, step, msg),
        )
    except LoginFailed:
        delete_credentials(tag)
        failed("login_failed", "Flex Bucks didn't accept the login from your invite. "
                               "Remove our address from your Flex Bucks guests, add it again, then tap Start over.")
        return
    except LoginLimited:
        failed("login_limited", "Flex Bucks is limiting logins right now. "
                                "Your invite is saved, so wait about 15 minutes and tap Start over.")
        return
    except NoAccounts:
        failed("no_accounts", "We logged in, but Flex Bucks isn't sharing any accounts with your guest. "
                              "Check that you're on a meal plan and that, in Grant Guest Access, the guest "
                              "can view your account activity. Then tap Start over.")
        return
    except Exception:
        _log(f"[job {job_id}] scrape failed:\n{traceback.format_exc()}")
        failed("scrape_error", "We couldn't load your Flex Bucks history right now. Try again in a few minutes.")
        return

    _log(f"[job {job_id}] fetched {len(transactions)} purchases, {len(swipes)} meal swipes")
    _archive_run(job_id, tag, student, invite, transactions, swipes, balances, pages)
    if not transactions and not swipes:
        failed("no_activity", f"We didn't find any Flex Bucks purchases or meal swipes for {label}. "
                              "If you're not on a meal plan, there's nothing to wrap yet!")
        return

    metadata = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "startdate": startdate, "enddate": enddate,
        "name": invite.get("name"),
        "balances": balances,
        "weekly_meal_plans": weekly_meal_plans(pages),
        "student_key": student,
        "run_id": job_id,
    }
    ai = None
    if analysis.enabled():
        update_progress(job_id, 5, "Personalizing your Wrapped…")
        try:
            analysis.normalize(transactions)
            ai = analysis.analyze(_summary(transactions, swipes, metadata, label))
        except Exception:
            _log(f"[job {job_id}] DeepSeek pass failed, continuing without it:\n{traceback.format_exc()}")

    update_progress(job_id, 6, "Wrapping it up…")
    share_id = _save_result(transactions, swipes, metadata, ai)
    _record_run(transactions, swipes, student, metadata)
    analytics.record("wrap_finished", visitor=analytics.visitor_id(tag), student=student, share_id=share_id,
                     name=invite.get("name"), purchases=len(transactions), swipes=len(swipes), ai=ai is not None)
    finish(job_id, share_id)


def _archive_run(job_id, tag, student, invite, transactions, swipes, balances, pages) -> None:
    """Keep everything every scrape returned, forever, for later analytics: the raw
    statement HTML per account plus what we parsed from it. One file per run, never
    rewritten, so re-runs and re-parses can be compared over time. No passwords."""
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    with open(os.path.join(ARCHIVE_DIR, f"{stamp}-{job_id}.json"), "w", encoding="utf-8") as f:
        json.dump({
            "run_id": job_id, "archived_at": stamp, "inbox": tag, "student_key": student,
            "name": invite.get("name"), "guest_login": invite.get("username"),
            "balances": balances, "purchases": transactions, "swipes": swipes, "pages": pages,
        }, f)


def _summary(transactions: list, swipes: list, metadata: dict, label: str) -> dict:
    """Aggregated stats handed to DeepSeek for the personality/roast/money slides."""
    items = _aggregate_items(transactions)
    spots = defaultdict(lambda: {"visits": 0, "spend": 0.0, "items": set()})
    for tx in transactions:
        if spot_name(tx["location"]) in NOT_SPOTS:
            continue
        spot = spots[spot_name(tx["location"])]
        spot["visits"] += 1
        spot["spend"] += tx["grand_total"]
        spot["items"].update(c["item"] for c in tx.get("clean_items") or [])
        if "clean_items" not in tx:
            spot["items"].update(expand_name(i["name"]) for i in tx["items"])
    total_spend = round(sum(tx["grand_total"] for tx in transactions), 2)
    paid_by_flex = round(sum(flex_paid(tx) for tx in transactions), 2)
    times = time_insights(transactions, metadata["startdate"], metadata["enddate"])
    swipe_stats = time_insights(swipes, metadata["startdate"], metadata["enddate"])
    return {
        "semester": label,
        "visits": len(transactions),
        "total_spend": total_spend,
        "paid_with_flex": paid_by_flex,
        "paid_other_ways": round(total_spend - paid_by_flex, 2),
        "avg_per_visit": round(total_spend / len(transactions), 2) if transactions else 0,
        "top_items": [{**{k: i[k] for k in ("name", "qty", "total_spend")},
                       "bought_at": sorted(name for name, v in spots.items() if i["name"] in v["items"])}
                      for i in items[:10]],
        "unique_items": len(items),
        "spots": {k: {"visits": v["visits"], "spend": round(v["spend"], 2), "different_items": len(v["items"])}
                  for k, v in spots.items()},
        "earliest": times and times["earliest"],
        "latest": times and times["latest"],
        "avg_visits_per_weekday": times and {w["day"]: w["avg"] for w in times["weekdays"]},
        "flex_left": _flex_plan(transactions, metadata, swipes),
        "meal_plan_swipes_left": _meal_block(metadata),
        "meal_swipes": swipe_stats and {
            "total": swipe_stats["count"],
            "by_meal": {m["meal"]: m["count"] for m in swipe_stats["meal_totals"]},
            "by_weekday": {w["day"]: w["total"] for w in swipe_stats["weekdays"]},
            "earliest": swipe_stats["earliest"],
            "latest": swipe_stats["latest"],
        },
        **roast_facts(transactions, swipes, metadata),
    }


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    tag = request.cookies.get(INBOX_COOKIE)
    new_visitor = not is_tag(tag)
    if new_visitor:
        tag = _new_inbox_tag()
    vs_id = request.args.get("vs")
    vs = _result_meta(vs_id)
    invite = load_credentials(tag)
    returning = None
    if invite:
        mine_id = request.cookies.get(MINE_COOKIE)
        student_key = _student_key(invite, tag)
        returning = {
            "first_name": _first_name(invite) if invite.get("name") else "",
            "has_run": student_key in _load_stats().get("students", {}),
            "wrapped_url": url_for("wrapped", share_id=mine_id) if _result_meta(mine_id) else None,
        }
    if not analytics.is_bot(request.user_agent.string):
        analytics.record("landing_view", visitor=analytics.visitor_id(tag), share_id=vs_id if vs else None,
                         new_visitor=new_visitor, returning=bool(invite), from_friend=bool(vs))
    resp = Response(render_template(
        "index.html", inbox_address=inbox_address(tag), semester_label=_semester_dates()[2],
        vs_name=_first_name(vs) if vs else None, returning=returning,
    ))
    _set_cookie(resp, INBOX_COOKIE, tag)
    if vs:
        _set_cookie(resp, VS_COOKIE, vs_id, max_age=30 * 24 * 3600)
    return resp


@app.get("/help")
def setup_help():
    tag = request.cookies.get(INBOX_COOKIE)
    if not is_tag(tag):
        tag = _new_inbox_tag()
    resp = Response(render_template("help.html", inbox_address=inbox_address(tag)))
    _set_cookie(resp, INBOX_COOKIE, tag)
    return resp


@app.post("/switch-account")
def switch_account():
    """Forget this browser's saved Flex guest login and start over with a fresh inbox."""
    tag = request.cookies.get(INBOX_COOKIE)
    if is_tag(tag):
        delete_credentials(tag)
    resp = redirect(url_for("index"))
    _set_cookie(resp, INBOX_COOKIE, _new_inbox_tag())
    resp.delete_cookie(MINE_COOKIE)
    return resp


@app.get("/campus")
def campus_page():
    students = list(_load_stats()["students"].values())
    if not analytics.is_bot(request.user_agent.string):
        analytics.record("campus_view", visitor=analytics.visitor_id(request.cookies.get(INBOX_COOKIE)))
    # Opened from a Wrapped (?from=<share_id>&at=<slide>): Back returns to that slide.
    from_id, at = request.args.get("from"), request.args.get("at")
    back_url = (url_for("wrapped", share_id=from_id, at=at if at in RETURN_SLIDES else None)
                if _result_meta(from_id) else None)
    return render_template(
        "campus.html", campus=campus_wrapped(students),
        semester_label=_semester_dates()[2],
        canonical=url_for("campus_page", _external=True, _scheme="https"),
        back_url=back_url,
    )


@app.post("/start")
def start():
    cleanup_old_jobs()
    tag = request.cookies.get(INBOX_COOKIE)
    if not is_tag(tag):
        flash("Your browser didn't keep our cookie. Enable cookies and try again.", "error")
        return redirect(url_for("index"))

    job_id = job_for(tag)
    if job_id is None:
        flash("We're swamped right now. Try again in a minute.", "error")
        return redirect(url_for("index"))
    invite = load_credentials(tag)
    analytics.record("wrap_started", visitor=analytics.visitor_id(tag),
                     student=_student_key(invite, tag) if invite else None, waiting_for_invite=not invite)
    if invite and claim(job_id):
        _start_job(job_id, tag, invite)
    return redirect(url_for("wait", job_id=job_id))


@app.get("/wait/<job_id>")
def wait(job_id):
    job = get_job(job_id)
    if not job:
        flash("That run expired. Start again below.", "error")
        return redirect(url_for("index"))
    return render_template("waiting.html", job=job, inbox_address=inbox_address(job["tag"]))


@app.get("/status/<job_id>")
def status(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"status": "error", "message": "That run expired. Tap Start over."}), 404

    resp = jsonify({
        "status": job["status"],
        "step": job["step"],
        "total_steps": job["total_steps"],
        "message": job["message"],
        "redirect": url_for("wrapped", share_id=job["share_id"]) if job["status"] == "done" else None,
        "error": job["error"],
    })
    if job["status"] == "done":
        _set_cookie(resp, MINE_COOKIE, job["share_id"])
    return resp


@app.post("/api/inbound")
def api_inbound():
    """Raw invite email forwarded by the Cloudflare Email Worker."""
    secret = os.environ.get("CREDENTIAL_SECRET", "")
    provided = request.headers.get("X-Credential-Secret", "")
    if not secret or not hmac.compare_digest(provided.encode(), secret.encode()):
        return jsonify({"error": "unauthorized"}), 401

    recipient = request.headers.get("X-Envelope-To", "")
    tag = tag_from_recipient(recipient)
    if not tag:
        _log(f"[inbound] ignoring mail for unknown recipient {recipient!r}")
        analytics.record("invite_rejected", reason="unknown_recipient")
        return jsonify({"error": "unknown recipient"}), 422

    raw = request.get_data()
    if not is_atrium_invite(raw):
        _log(f"[inbound] dropped mail for inbox {tag[:2]}... that is not an Atrium invite")
        analytics.record("invite_rejected", visitor=analytics.visitor_id(tag), reason="not_atrium")
        return jsonify({"error": "not an Atrium invite"}), 422

    invite = parse_invite(raw, tag)
    if not invite:
        _log(f"[inbound] no password in {len(raw)}-byte email for inbox {tag[:2]}...")
        analytics.record("invite_rejected", visitor=analytics.visitor_id(tag), reason="no_password")
        return jsonify({"error": "no credentials found"}), 422

    save_credentials(tag, invite)
    analytics.record("invite_received", visitor=analytics.visitor_id(tag),
                     student=_student_key(invite, tag), name=invite.get("name"))
    _notify_signup(invite.get("name"))
    woken = claim_waiting(tag)
    for job_id in woken:
        _start_job(job_id, tag, invite)
    _log(f"[inbound] stored invite for inbox {tag[:2]}..., started {len(woken)} waiting run(s)")
    return jsonify({"ok": True})


@app.get("/results/<share_id>")
def results(share_id):
    result = _load_result(share_id)
    meta = result["metadata"]
    analytics.record("items_view", visitor=analytics.visitor_id(request.cookies.get(INBOX_COOKIE)),
                     student=meta.get("student_key"), share_id=share_id)
    items = _aggregate_items(result["transactions"])
    start, end = date.fromisoformat(meta["startdate"]), date.fromisoformat(meta["enddate"])
    return render_template(
        "dashboard.html",
        items=items,
        first_name=_first_name(meta) if meta.get("name") else "",
        period=f"{'Spring' if start.month <= 6 else 'Fall'} {start.year} · "
               f"{start:%b} {start.day} – {end:%b} {end.day}",
        total_qty=sum(i["qty"] for i in items),
        total_spend=round(sum(i["total_spend"] for i in items), 2),
        unique_items=len(items),
        share_id=share_id,
    )


def _wrapped_context(share_id: str) -> dict:
    """Everything the slides, the share cards and the link preview show."""
    result = _load_result(share_id)
    transactions = result["transactions"]
    meta = result["metadata"]
    items = _aggregate_items(transactions)
    total_qty = sum(i["qty"] for i in items)
    total_spend = round(sum(i["total_spend"] for i in items), 2)

    start = date.fromisoformat(meta["startdate"])
    semester_label = f"{'Spring' if start.month <= 6 else 'Fall'} {start.year}"
    times = time_insights(transactions, meta["startdate"], meta["enddate"])
    swipes = time_insights(result.get("swipes", []), meta["startdate"], meta["enddate"])

    current = _student_summary(transactions, result.get("swipes", []))
    current["flex_short"] = _flex_short(transactions, meta, result.get("swipes", []))
    stored = _load_stats()["students"]
    student_key = meta.get("student_key")
    students = []
    replaced = False
    for key, summary in stored.items():
        if student_key and key == student_key:
            students.append(current)
            replaced = True
        else:
            students.append(summary)
    if not replaced:
        students.append(current)
    campus = campus_percentiles(current, students)
    usual = signature_order(transactions)
    badges = dining_badges(transactions, result.get("swipes", []), meta)
    summary = summary_highlights(transactions, result.get("swipes", []), meta)

    name = meta.get("name") or ""
    return {
        "share_id": share_id,
        "student_key": meta.get("student_key"),
        "student_name": name,
        "first_name": name.split()[0] if name else "",
        "semester_label": semester_label,
        "season": semester_label.split()[0],
        "year": start.year,
        "visits": len(transactions),
        "total_qty": total_qty,
        "total_spend": total_spend,
        "unique_items": len(items),
        "items": items[:5],
        "times": times,
        "swipes": swipes,
        "swipe_top_meal": max(swipes["meal_totals"], key=lambda m: m["count"])["meal"] if swipes else None,
        "tiger": tiger_express(result.get("swipes", []), meta),
        "flex": _flex_plan(transactions, meta, result.get("swipes", [])),
        "flex_campus": _flex_campus(students),
        "nuggets": nuggets if (nuggets := nuggets_eaten(transactions)) >= NUGGET_SLIDE_MINIMUM else None,
        "meal_block": _meal_block(meta),
        "flex_paid": round(sum(flex_paid(tx) for tx in transactions), 2),
        "ai": result.get("analysis"),
        "campus": campus,
        "usual": usual,
        "badges": badges,
        "streaks": dining_streaks(transactions, result.get("swipes", []), meta),
        "summary": summary,
    }


def _viewer(share_id: str) -> dict:
    """Whether the person viewing a Wrapped made it, and the comparison its outro offers."""
    meta = _result_meta(share_id) or {}
    tag = request.cookies.get(INBOX_COOKIE)
    invite = load_credentials(tag) if is_tag(tag) else None
    mine_id = request.cookies.get(MINE_COOKIE)
    mine = _result_meta(mine_id)
    owner = (mine_id == share_id
             or (mine is not None and _same_student(mine, meta))
             or (invite is not None and meta.get("student_key") == _student_key(invite, tag)))
    if owner:
        vs_id = request.cookies.get(VS_COOKIE)
        vs = _result_meta(vs_id)
        if vs is None or _same_student(vs, meta):
            return {"owner": True}
        return {"owner": True, "versus_name": _first_name(vs),
                "versus_url": url_for("compare_results", left_id=share_id, right_id=vs_id)}
    if mine is not None:
        return {"owner": False, "versus_url": url_for("compare_results", left_id=mine_id, right_id=share_id)}
    return {"owner": False, "get_own_url": url_for("index", vs=share_id)}


# The nugget-count slide shows from this many nuggets (about four 12-counts).
NUGGET_SLIDE_MINIMUM = 50

# Shareable slides: /wrapped/<id>/<slide> opens the deck at that slide, and its link
# preview (iMessage, Discord...) is the slide itself once the owner's browser has
# uploaded its rendering; until then it falls back to the Wrapped's preview card.
SLIDE_TITLES = {
    "intro": "Semester in food", "spent": "Total spent", "obsession": "#1 obsession",
    "usual": "Usual order", "nuggets": "Nugget count", "personality": "Dining personality", "badges": "Badges",
    "spots": "Favorite spots", "earliest-latest": "Earliest & latest bite", "caf-swipes": "Caf swipes",
    "tiger-express": "Tiger Express", "streak": "Caf streak", "flex-left": "Flex Bucks",
    "roast": "The roast", "money-talk": "Money talk", "rankings": "Campus rankings",
    "summary": "Semester recap",
}
SLIDE_IMAGE_MAX_BYTES = 4 * 1024 * 1024


def _slide_image_path(share_id: str, slide: str) -> str:
    return os.path.join(OG_DIR, f"{share_id}--{slide}.png")


def _png_size(head: bytes) -> tuple[int, int] | None:
    """(width, height) from a PNG's IHDR, or None if it isn't a PNG."""
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def _uploaded_slides(share_id: str) -> list[str]:
    return [slide for slide in SLIDE_TITLES if os.path.exists(_slide_image_path(share_id, slide))]


# Slides that link out (to the Campus Leaderboard) and so can be returned to with ?at=.
RETURN_SLIDES = {"rankings", "outro"}


def _render_wrapped(share_id: str, slide: str | None = None, start: str | None = None) -> Response:
    ctx = _wrapped_context(share_id)
    viewer = _viewer(share_id)
    if not analytics.is_bot(request.user_agent.string):
        analytics.record("wrapped_view", visitor=analytics.visitor_id(request.cookies.get(INBOX_COOKIE)),
                         student=ctx.get("student_key"), share_id=share_id, owner=viewer["owner"], slide=slide)
    share_url = url_for("wrapped", share_id=share_id, _external=True, _scheme="https")
    og = {"url": share_url, "label": None, "width": 1200, "height": 630,
          "image": url_for("og_result", share_id=share_id, _external=True, _scheme="https")}
    if slide:
        og.update(url=url_for("wrapped_slide", share_id=share_id, slide=slide, _external=True, _scheme="https"),
                  label=SLIDE_TITLES[slide])
        path = _slide_image_path(share_id, slide)
        if os.path.exists(path):
            with open(path, "rb") as f:
                og["width"], og["height"] = _png_size(f.read(24)) or (1080, 1920)
            og["image"] = url_for("og_slide", share_id=share_id, slide=slide, _external=True, _scheme="https")
    resp = Response(render_template(
        "wrapped.html",
        **ctx,
        viewer=viewer,
        og=og,
        start_slide=slide or start,
        uploaded_slides=_uploaded_slides(share_id) if viewer["owner"] else [],
        dashboard_url=url_for("results", share_id=share_id),
        share_url=share_url,
        compare_url=url_for("compare_start", share_id=share_id),
    ))
    if viewer["owner"] and request.cookies.get(MINE_COOKIE) != share_id:
        # Owners who generated before this cookie existed: remember it for friends' links.
        _set_cookie(resp, MINE_COOKIE, share_id)
    return resp


@app.get("/wrapped/<share_id>")
def wrapped(share_id):
    # ?at=<slide> reopens the deck where the viewer left it (e.g. back from the leaderboard).
    at = request.args.get("at")
    return _render_wrapped(share_id, start=at if at in RETURN_SLIDES else None)


@app.get("/wrapped/<share_id>/<slide>")
def wrapped_slide(share_id, slide):
    if slide not in SLIDE_TITLES:
        return redirect(url_for("wrapped", share_id=share_id))
    return _render_wrapped(share_id, slide)


@app.post("/api/slide-image/<share_id>/<slide>")
def upload_slide_image(share_id, slide):
    """The owner's browser uploads each slide's 1080-wide rendering for link previews."""
    if slide not in SLIDE_TITLES or _result_meta(share_id) is None or not _viewer(share_id)["owner"]:
        return "", 404
    if (request.content_length or 0) > SLIDE_IMAGE_MAX_BYTES:
        return "", 413
    data = request.get_data()
    size = _png_size(data[:24])
    if not size or size[0] != 1080 or not 1700 <= size[1] <= 2100 or len(data) > SLIDE_IMAGE_MAX_BYTES:
        return "", 400
    os.makedirs(OG_DIR, exist_ok=True)
    path = _slide_image_path(share_id, slide)
    with open(path + ".tmp", "wb") as f:
        f.write(data)
    os.replace(path + ".tmp", path)
    return "", 204


def _comparison_profile(share_id: str) -> dict:
    result = _load_result(share_id)
    transactions = result["transactions"]
    metadata = result["metadata"]
    items = _aggregate_items(transactions)
    name = metadata.get("name") or ""
    year = date.fromisoformat(metadata["startdate"]).year
    caf_times = [
        when for swipe in result.get("swipes", [])
        if (when := parse_when(swipe.get("date", ""), year))
    ]
    return {
        "share_id": share_id,
        "name": name,
        "first_name": name.split()[0] if name else "Student",
        "total_spend": round(sum(item["total_spend"] for item in items), 2),
        "visits": len(transactions),
        "caf_swipes": len(result.get("swipes", [])),
        "top_item": items[0]["name"] if items else "No favorite yet",
        "items": {item["name"]: item["qty"] for item in items},
        "usual": signature_order(transactions),
        "badges": dining_badges(transactions, result.get("swipes", []), metadata),
        "_caf_times": caf_times,
    }


def _share_id_from_input(value: str) -> str | None:
    value = (value or "").strip().rstrip("/")
    if _SHARE_ID_RE.fullmatch(value):
        return value
    path = urlparse(value).path.rstrip("/")
    candidate = path.rsplit("/", 1)[-1]
    return candidate if _SHARE_ID_RE.fullmatch(candidate) else None


@app.route("/compare/<share_id>", methods=["GET", "POST"])
def compare_start(share_id):
    mine = _comparison_profile(share_id)
    if request.method == "POST":
        other_id = _share_id_from_input(request.form.get("friend", ""))
        if not other_id or not os.path.isfile(os.path.join(RESULTS_DIR, f"{other_id}.json")):
            flash("That doesn't look like a Stu Wrapped link. Paste the full link your friend sent.", "error")
        elif other_id == share_id:
            flash("That's the same Wrapped. Ask a friend for theirs!", "error")
        else:
            return redirect(url_for("compare_results", left_id=share_id, right_id=other_id))
    invite_url = url_for("compare_start", share_id=share_id, _external=True, _scheme="https")
    og = url_for("og_result", share_id=share_id, _external=True, _scheme="https")
    return render_template("compare_start.html", mine=mine, invite_url=invite_url, og_image=og)


@app.get("/compare/<left_id>/<right_id>")
def compare_results(left_id, right_id):
    if left_id == right_id:
        flash("Pick two different Wrapped links to compare.", "error")
        return redirect(url_for("compare_start", share_id=left_id))
    left = _comparison_profile(left_id)
    right = _comparison_profile(right_id)
    if _same_student(_result_meta(left_id), _result_meta(right_id)):
        flash("Those links belong to the same student. Ask a friend for theirs!", "error")
        return redirect(url_for("compare_start", share_id=left_id))
    match = friend_match(left, right)
    if not analytics.is_bot(request.user_agent.string):
        analytics.record("compare_view", visitor=analytics.visitor_id(request.cookies.get(INBOX_COOKIE)),
                         student=(_result_meta(left_id) or {}).get("student_key"), share_id=left_id,
                         other=right_id, other_student=(_result_meta(right_id) or {}).get("student_key"))
    share_url = url_for(
        "compare_results", left_id=left_id, right_id=right_id,
        _external=True, _scheme="https",
    )
    return render_template(
        "compare.html", left=left, right=right, match=match, share_url=share_url,
        share_title=f"{left['first_name']} + {right['first_name']} · OBU Stu Wrapped",
    )


@app.get("/og/<share_id>.png")
def og_result(share_id):
    ctx = _wrapped_context(share_id)
    analytics.record("link_preview", student=ctx.get("student_key"), share_id=share_id,
                     app=analytics.preview_app(request.user_agent.string))
    path = os.path.join(OG_DIR, f"{share_id}.png")
    if not os.path.exists(path):
        os.makedirs(OG_DIR, exist_ok=True)
        subtitle = " · ".join(filter(None, [
            ctx["ai"]["personality"]["title"] if ctx["ai"] else None,
            f"{ctx['swipes']['count']} Caf swipes" if ctx["swipes"] else None,
            f"${ctx['flex_paid']:,.2f} on Flex" if ctx["flex_paid"] else None,
        ]))
        heading = f"{ctx['first_name']}'s {ctx['semester_label']}" if ctx["first_name"] else ctx["semester_label"]
        og_image.render(path, heading, subtitle)
    return send_file(path, mimetype="image/png", max_age=86400)


@app.get("/og/<share_id>/<slide>.png")
def og_slide(share_id, slide):
    """A slide's uploaded rendering, or the Wrapped's preview card until there is one."""
    if slide not in SLIDE_TITLES or not _SHARE_ID_RE.fullmatch(share_id):
        abort(404)
    path = _slide_image_path(share_id, slide)
    if not os.path.exists(path):
        return og_result(share_id)
    meta = _result_meta(share_id) or {}
    analytics.record("link_preview", student=meta.get("student_key"), share_id=share_id, slide=slide,
                     app=analytics.preview_app(request.user_agent.string))
    return send_file(path, mimetype="image/png", max_age=3600)


@app.get("/og/site.png")
def og_site():
    label = _semester_dates()[2]
    path = os.path.join(OG_DIR, f"site-{label.replace(' ', '-')}.png")
    if not os.path.exists(path):
        os.makedirs(OG_DIR, exist_ok=True)
        og_image.render(path, label, "Your semester of CFA runs, Dr. Jack's lattes and Caf swipes")
    return send_file(path, mimetype="image/png", max_age=86400)


@app.get("/robots.txt")
def robots_txt():
    # Twitterbot honours robots.txt, so /wrapped and /og stay crawlable for link
    # previews; personal pages stay out of search through their noindex meta tag.
    sitemap = url_for("sitemap_xml", _external=True, _scheme="https")
    return Response(f"User-agent: *\nDisallow: /api/\nDisallow: /status/\n\nSitemap: {sitemap}\n",
                    mimetype="text/plain")


@app.get("/sitemap.xml")
def sitemap_xml():
    pages = [url_for("index", _external=True, _scheme="https"),
             url_for("campus_page", _external=True, _scheme="https")]
    return Response(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"  <url><loc>{page}</loc><changefreq>weekly</changefreq></url>\n" for page in pages)
        + "</urlset>\n",
        mimetype="application/xml",
    )


@app.errorhandler(404)
def not_found(_error):
    return render_template(
        "error.html", heading="Nothing here",
        message="That link doesn't lead anywhere. It may be mistyped, or that Wrapped was deleted.",
    ), 404


@app.errorhandler(500)
def server_error(_error):
    return render_template(
        "error.html", heading="Something broke",
        message="That one's on us. Try again in a minute.",
    ), 500


@app.get("/api/results/<share_id>")
def api_results(share_id):
    """Raw receipts: only for the student who made this Wrapped."""
    result = _load_result(share_id)
    if not _viewer(share_id)["owner"]:
        abort(404)
    return jsonify(result)


@app.get("/download/<share_id>/csv")
def download_csv(share_id):
    result = _load_result(share_id)
    analytics.record("csv_download", visitor=analytics.visitor_id(request.cookies.get(INBOX_COOKIE)),
                     student=result["metadata"].get("student_key"), share_id=share_id)
    items = _aggregate_items(result["transactions"])
    lines = ["rank,item,qty,total_spend"]
    for item in items:
        safe = item["name"].replace('"', '""')
        lines.append(f'{item["rank"]},"{safe}",{item["qty"]},{item["total_spend"]:.2f}')

    return Response(
        "\n".join(lines) + "\n",
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=stu_wrapped.csv"},
    )


@app.post("/api/event")
def api_event():
    """Browser-side analytics (shares, copied email, how far into the deck). Only names
    in analytics.CLIENT_EVENTS, only their listed properties, short string values."""
    if (request.content_length or 0) > 2048:
        return "", 400
    data = request.get_json(silent=True, force=True)
    if not isinstance(data, dict):
        return "", 400
    name = data.get("name")
    allowed = analytics.CLIENT_EVENTS.get(name)
    if allowed is None:
        return "", 400
    props = {}
    for key in allowed:
        value = data.get(key)
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            continue
        props[key] = value[:40] if isinstance(value, str) else value
    if "method" in props and props["method"] not in analytics.SHARE_METHODS:
        return "", 400
    share_id = data.get("share_id")
    meta = _result_meta(share_id) if isinstance(share_id, str) else None
    analytics.record(name, visitor=analytics.visitor_id(request.cookies.get(INBOX_COOKIE)),
                     student=meta.get("student_key") if meta else None,
                     share_id=share_id if meta else None, **props)
    return "", 204


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", port=port)
