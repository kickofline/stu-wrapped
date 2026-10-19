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

from dotenv import load_dotenv
from flask import (Flask, Response, abort, flash, jsonify, redirect,
                   render_template, request, send_file, url_for)

load_dotenv()

import analysis
import og_image
from insights import parse_when, spot_name, time_insights
from social import (campus_percentiles, dining_badges, friend_match,
                    signature_order, summary_highlights)
from jobs import (active_job_for, claim, claim_waiting, cleanup_old_jobs,
                  create_job, fail, finish, get_job, update_progress)
from mailbox import (delete_credentials, is_tag, load_credentials, new_tag,
                     parse_invite, plus_address, save_credentials,
                     tag_from_recipient)
from name_map import expand_name
from scraper import LoginFailed, LoginLimited, NoAccounts, fetch_activity

app = Flask(__name__)
app.secret_key = "LUKERMKNGAONMXXMGJYAAGYMRIVVECNV"
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # raw invite emails

DATA_DIR       = os.path.join(os.path.dirname(__file__), "data")
STATS_FILE     = os.path.join(DATA_DIR, "stats.json")
RAW_ITEMS_FILE = os.path.join(DATA_DIR, "raw_items.json")
RESULTS_DIR    = os.path.join(DATA_DIR, "results")
OG_DIR         = os.path.join(DATA_DIR, "og")  # rendered link-preview images, one per share link
ARCHIVE_DIR    = os.path.join(DATA_DIR, "archive")  # every scrape, raw + parsed, kept forever

INBOX_COOKIE = "inbox"
_SHARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{12}$")
_stats_lock = threading.Lock()


def _log(*args):
    print(*args, flush=True)


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


def _load_stats() -> dict:
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


def _student_summary(transactions: list, swipes: list) -> dict:
    items = _aggregate_items(transactions)
    spots = defaultdict(int)
    for transaction in transactions:
        spots[spot_name(transaction["location"])] += 1
    return {
        "total_qty": sum(item["qty"] for item in items),
        "total_spend": round(sum(item["total_spend"] for item in items), 2),
        "unique_items": len(items),
        "top_item": items[0]["name"] if items else None,
        "visits": len(transactions),
        "caf_swipes": len(swipes),
        "items": {item["name"]: item["qty"] for item in items},
        "spots": dict(spots),
    }


def _record_run(transactions: list, swipes: list, student_key: str) -> None:
    """Store this student's latest run in the campus-wide comparison stats."""
    summary = _student_summary(transactions, swipes)
    summary["updated"] = datetime.now().isoformat(timespec="seconds")
    with _stats_lock:
        _record_raw_items(transactions)
        stats = _load_stats()
        stats["students"][student_key] = summary
        _save_stats(stats)


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


def _flex_plan(transactions: list, meta: dict) -> dict | None:
    """Flex left, what it takes to use it up by the end of finals, and where the
    current pace lands. None when the run has no Flex balance or the semester is over."""
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

    plan = {
        "balance": balance,
        "end": f"{end:%B} {end.day}",
        "days_left": days_left,
        "per_day": round(balance / days_left, 2),
        "per_week": round(balance / days_left * 7, 2),
        "pace_per_week": None, "projected_left": None, "runs_out": None,
    }
    dates = [d for d in (parse_when(tx["date"], start.year) for tx in transactions) if d]
    if dates:
        spent = sum(tx["grand_total"] for tx in transactions)
        pace = spent / max(1, (today - min(dates).date()).days + 1)  # dollars per day so far
        plan["pace_per_week"] = round(pace * 7, 2)
        projected = balance - pace * days_left
        if projected >= 0:
            plan["projected_left"] = round(projected, 2)
        else:
            out = today + timedelta(days=int(balance / pace))
            plan["runs_out"] = f"{out:%B} {out.day}"
    return plan


# ── Results (persisted so share links outlive restarts) ─────────────────────

def _save_result(transactions: list, swipes: list, metadata: dict, ai: dict | None) -> str:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    share_id = secrets.token_urlsafe(9)
    with open(os.path.join(RESULTS_DIR, f"{share_id}.json"), "w", encoding="utf-8") as f:
        json.dump({"transactions": transactions, "swipes": swipes, "metadata": metadata, "analysis": ai}, f)
    return share_id


def _load_result(share_id: str) -> dict:
    if not _SHARE_ID_RE.match(share_id):
        abort(404)
    try:
        with open(os.path.join(RESULTS_DIR, f"{share_id}.json"), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        abort(404)


# ── Jobs ─────────────────────────────────────────────────────────────────────

def _start_job(job_id: str, tag: str, invite: dict) -> None:
    threading.Thread(target=_run_job, args=(job_id, tag, invite), daemon=True).start()


def _run_job(job_id: str, tag: str, invite: dict) -> None:
    startdate, enddate, label = _semester_dates()
    try:
        transactions, swipes, balances, pages = fetch_activity(
            invite["username"], invite["password"], startdate, enddate,
            progress=lambda step, msg: update_progress(job_id, step, msg),
        )
    except LoginFailed:
        delete_credentials(tag)
        fail(job_id, "Flex Bucks didn't accept the login from your invite email. "
                     "Remove our address from your Flex Bucks guests, add it again, then hit Generate.")
        return
    except LoginLimited:
        fail(job_id, "Flex Bucks is limiting logins for your guest right now. "
                     "Wait about 15 minutes and hit Generate again; your invite is saved.")
        return
    except NoAccounts:
        fail(job_id, "We could log in, but Flex Bucks isn't showing your guest any accounts. "
                     "Check that you're on a meal plan, then in Grant Guest Access make sure the guest "
                     "is allowed to view your account activity, and hit Generate again.")
        return
    except Exception:
        _log(f"[job {job_id}] scrape failed:\n{traceback.format_exc()}")
        fail(job_id, "We couldn't load your Flex Bucks history right now. Try again in a few minutes.")
        return

    _log(f"[job {job_id}] fetched {len(transactions)} purchases, {len(swipes)} meal swipes")
    student = _student_key(invite, tag)
    _archive_run(job_id, tag, student, invite, transactions, swipes, balances, pages)
    if not transactions and not swipes:
        fail(job_id, f"We didn't find any Flex Bucks purchases or meal swipes for {label}. "
                     "If you're not on a meal plan, there's nothing to wrap yet!")
        return

    metadata = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "startdate": startdate, "enddate": enddate,
        "name": invite.get("name"),
        "balances": balances,
        "student_key": student,
        "run_id": job_id,
    }
    ai = None
    if analysis.enabled():
        update_progress(job_id, 5, "Personalizing your Wrapped...")
        try:
            analysis.normalize(transactions)
            ai = analysis.analyze(_summary(transactions, swipes, metadata, label))
        except Exception:
            _log(f"[job {job_id}] DeepSeek pass failed, continuing without it:\n{traceback.format_exc()}")

    update_progress(job_id, 6, "Wrapping it up...")
    share_id = _save_result(transactions, swipes, metadata, ai)
    _record_run(transactions, swipes, student)
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
    spots = defaultdict(lambda: {"visits": 0, "spend": 0.0})
    for tx in transactions:
        spot = spots[spot_name(tx["location"])]
        spot["visits"] += 1
        spot["spend"] += tx["grand_total"]
    total_spend = round(sum(tx["grand_total"] for tx in transactions), 2)
    times = time_insights(transactions, metadata["startdate"], metadata["enddate"])
    swipe_stats = time_insights(swipes, metadata["startdate"], metadata["enddate"])
    return {
        "semester": label,
        "visits": len(transactions),
        "total_spend": total_spend,
        "avg_per_visit": round(total_spend / len(transactions), 2) if transactions else 0,
        "top_items": [{k: i[k] for k in ("name", "qty", "total_spend")} for i in items[:10]],
        "unique_items": len(items),
        "spots": {k: {"visits": v["visits"], "spend": round(v["spend"], 2)} for k, v in spots.items()},
        "earliest": times and times["earliest"],
        "latest": times and times["latest"],
        "avg_visits_per_weekday": times and {w["day"]: w["avg"] for w in times["weekdays"]},
        "flex_left": _flex_plan(transactions, metadata),
        "meal_swipes": swipe_stats and {
            "total": swipe_stats["count"],
            "by_meal": {m["meal"]: m["count"] for m in swipe_stats["meal_totals"]},
            "by_weekday": {w["day"]: w["total"] for w in swipe_stats["weekdays"]},
            "earliest": swipe_stats["earliest"],
            "latest": swipe_stats["latest"],
        },
    }


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    tag = request.cookies.get(INBOX_COOKIE)
    if not is_tag(tag):
        tag = new_tag()
    resp = Response(render_template("index.html", plus_address=plus_address(tag), semester_label=_semester_dates()[2]))
    resp.set_cookie(INBOX_COOKIE, tag, max_age=365 * 24 * 3600, httponly=True, samesite="Lax",
                    secure=request.is_secure or request.headers.get("X-Forwarded-Proto") == "https")
    return resp


@app.post("/start")
def start():
    cleanup_old_jobs()
    tag = request.cookies.get(INBOX_COOKIE)
    if not is_tag(tag):
        flash("Your browser didn't keep our cookie. Enable cookies and try again.", "error")
        return redirect(url_for("index"))

    job = active_job_for(tag)
    job_id = job["job_id"] if job else create_job(tag)
    invite = load_credentials(tag)
    if invite and claim(job_id):
        _start_job(job_id, tag, invite)
    return redirect(url_for("wait", job_id=job_id))


@app.get("/wait/<job_id>")
def wait(job_id):
    job = get_job(job_id)
    if not job:
        flash("That run expired. Hit Generate again.", "error")
        return redirect(url_for("index"))
    return render_template("waiting.html", job=job, plus_address=plus_address(job["tag"]))


@app.get("/status/<job_id>")
def status(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"status": "error", "message": "That run expired. Hit Generate again."}), 404

    return jsonify({
        "status": job["status"],
        "step": job["step"],
        "total_steps": job["total_steps"],
        "message": job["message"],
        "redirect": url_for("wrapped", share_id=job["share_id"]) if job["status"] == "done" else None,
        "error": job["error"],
    })


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
        return jsonify({"error": "unknown recipient"}), 422

    invite = parse_invite(request.get_data(), tag)
    if not invite:
        _log(f"[inbound] no password in {len(request.get_data())}-byte email for inbox {tag[:4]}...")
        return jsonify({"error": "no credentials found"}), 422

    save_credentials(tag, invite)
    woken = claim_waiting(tag)
    for job_id in woken:
        _start_job(job_id, tag, invite)
    _log(f"[inbound] stored invite for inbox {tag[:4]}..., started {len(woken)} waiting run(s)")
    return jsonify({"ok": True})


@app.get("/results/<share_id>")
def results(share_id):
    result = _load_result(share_id)
    items = _aggregate_items(result["transactions"])
    return render_template(
        "dashboard.html",
        items=items,
        items_json=json.dumps(items),
        metadata=result["metadata"],
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
        "flex": _flex_plan(transactions, meta),
        "ai": result.get("analysis"),
        "campus": campus,
        "usual": usual,
        "badges": badges,
        "summary": summary,
    }


@app.get("/wrapped/<share_id>")
def wrapped(share_id):
    ctx = _wrapped_context(share_id)
    return render_template(
        "wrapped.html",
        **ctx,
        dashboard_url=url_for("results", share_id=share_id),
        share_url=url_for("wrapped", share_id=share_id, _external=True, _scheme="https"),
        og_image=url_for("og_result", share_id=share_id, _external=True, _scheme="https"),
        compare_url=url_for("compare_start", share_id=share_id),
    )


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
        "_student_key": metadata.get("student_key"),
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
            flash("Paste a valid Stu Wrapped link or 12-character share code.", "error")
        elif other_id == share_id:
            flash("That is the same Wrapped. Ask a friend for theirs!", "error")
        else:
            return redirect(url_for("compare_results", left_id=share_id, right_id=other_id))
    invite_url = url_for("compare_start", share_id=share_id, _external=True, _scheme="https")
    return render_template("compare_start.html", mine=mine, invite_url=invite_url)


@app.get("/compare/<left_id>/<right_id>")
def compare_results(left_id, right_id):
    if left_id == right_id:
        flash("Pick two different Wrapped links to compare.", "error")
        return redirect(url_for("compare_start", share_id=left_id))
    left = _comparison_profile(left_id)
    right = _comparison_profile(right_id)
    same_key = left["_student_key"] and left["_student_key"] == right["_student_key"]
    left_name = " ".join(left["name"].casefold().split())
    right_name = " ".join(right["name"].casefold().split())
    same_legacy_name = (not left["_student_key"] or not right["_student_key"]) and left_name and left_name == right_name
    if same_key or same_legacy_name:
        flash("Those links belong to the same student. Ask a friend for theirs!", "error")
        return redirect(url_for("compare_start", share_id=left_id))
    match = friend_match(left, right)
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
    path = os.path.join(OG_DIR, f"{share_id}.png")
    if not os.path.exists(path):
        os.makedirs(OG_DIR, exist_ok=True)
        subtitle = " · ".join(filter(None, [
            ctx["ai"]["personality"]["title"] if ctx["ai"] else None,
            f"{ctx['swipes']['count']} Caf swipes" if ctx["swipes"] else None,
            f"${ctx['total_spend']:,.2f} on Flex" if ctx["total_spend"] else None,
        ]))
        heading = f"{ctx['first_name']}'s {ctx['semester_label']}" if ctx["first_name"] else ctx["semester_label"]
        og_image.render(path, heading, subtitle)
    return send_file(path, mimetype="image/png", max_age=86400)


@app.get("/og/site.png")
def og_site():
    label = _semester_dates()[2]
    path = os.path.join(OG_DIR, f"site-{label.replace(' ', '-')}.png")
    if not os.path.exists(path):
        os.makedirs(OG_DIR, exist_ok=True)
        og_image.render(path, label, "Your semester of CFA runs, Dr. Jack's lattes and Caf swipes")
    return send_file(path, mimetype="image/png", max_age=86400)


@app.get("/api/results/<share_id>")
def api_results(share_id):
    return jsonify(_load_result(share_id))


@app.get("/download/<share_id>/csv")
def download_csv(share_id):
    items = _aggregate_items(_load_result(share_id)["transactions"])
    lines = ["rank,item,qty,total_spend"]
    for item in items:
        safe = item["name"].replace('"', '""')
        lines.append(f'{item["rank"]},"{safe}",{item["qty"]},{item["total_spend"]:.2f}')

    return Response(
        "\n".join(lines) + "\n",
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=stu_wrapped.csv"},
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, port=port)
