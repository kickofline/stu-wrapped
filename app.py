import hmac
import json
import os
import re
import secrets
import threading
import traceback
from collections import defaultdict
from datetime import date, datetime

from dotenv import load_dotenv
from flask import (Flask, Response, abort, flash, jsonify, redirect,
                   render_template, request, url_for)

load_dotenv()

import analysis
from insights import spot_name, time_insights
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

INBOX_COOKIE = "inbox"
_SHARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{12}$")
_stats_lock = threading.Lock()


def _log(*args):
    print(*args, flush=True)


def _location_stats(transactions: list, keywords: tuple) -> tuple:
    """Sum qty and spend for transactions whose location matches any keyword."""
    qty = 0
    spend = 0.0
    for tx in transactions:
        if any(k in tx["location"].lower() for k in keywords):
            for item in tx["items"]:
                qty += item["qty"]
                spend += item.get("subtotal") or 0.0
    return qty, round(spend, 2)


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
    try:
        with open(STATS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"total_runs": 0, "all_total_qtys": [], "all_total_spends": [], "all_unique_items": [], "top_items": {}}


def _save_stats(stats: dict) -> None:
    os.makedirs(os.path.dirname(STATS_FILE), exist_ok=True)
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)


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


def _record_run(transactions: list) -> None:
    """Add one finished run to the campus-wide comparison stats."""
    items = _aggregate_items(transactions)
    with _stats_lock:
        _record_raw_items(transactions)
        stats = _load_stats()
        stats["total_runs"] += 1
        stats["all_total_qtys"].append(sum(i["qty"] for i in items))
        stats.setdefault("all_total_spends", []).append(round(sum(i["total_spend"] for i in items), 2))
        stats["all_unique_items"].append(len(items))
        if items:
            top = items[0]["name"]
            stats["top_items"][top] = stats["top_items"].get(top, 0) + 1
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
        transactions, swipes = fetch_activity(
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
    if not transactions and not swipes:
        fail(job_id, f"We didn't find any Flex Bucks purchases or meal swipes for {label}. "
                     "If you're not on a meal plan, there's nothing to wrap yet!")
        return

    metadata = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "startdate": startdate, "enddate": enddate,
        "name": invite.get("name"),
    }
    ai = None
    if analysis.enabled():
        update_progress(job_id, 5, "Asking DeepSeek to decode your orders...")
        try:
            analysis.normalize(transactions)
            ai = analysis.analyze(_summary(transactions, swipes, metadata, label))
        except Exception:
            _log(f"[job {job_id}] DeepSeek pass failed, continuing without it:\n{traceback.format_exc()}")

    update_progress(job_id, 6, "Wrapping it up...")
    share_id = _save_result(transactions, swipes, metadata, ai)
    _record_run(transactions)
    finish(job_id, share_id)


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
        "meal_swipes": swipe_stats and {
            "total": swipe_stats["count"],
            "by_meal": {m["meal"]: m["count"] for m in swipe_stats["meal_totals"]},
            "avg_per_weekday": {w["day"]: w["avg"] for w in swipe_stats["weekdays"]},
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
    resp = Response(render_template("index.html", plus_address=plus_address(tag)))
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


@app.get("/wrapped/<share_id>")
def wrapped(share_id):
    result = _load_result(share_id)
    transactions = result["transactions"]
    meta = result["metadata"]
    all_items = _aggregate_items(transactions)
    top_items = all_items[:3]
    total_qty = sum(i["qty"] for i in all_items)
    total_spend = round(sum(i["total_spend"] for i in all_items), 2)
    unique_items = len(all_items)

    # ── Weekly rate ──────────────────────────────────────────
    try:
        start = date.fromisoformat(meta["startdate"])
        end   = date.fromisoformat(meta["enddate"])
        weeks = max(1, (end - start).days / 7)
    except Exception:
        weeks = 16
    items_per_week = round(total_qty / weeks, 1)

    # ── Per-location stats (keyed on transaction location name) ─
    cfa_qty,     cfa_spend     = _location_stats(transactions, ("cfa",))
    tacos_qty,   tacos_spend   = _location_stats(transactions, ("taco",))
    drjacks_qty, drjacks_spend = _location_stats(transactions, ("dr jack", "dr. jack", "drjack"))

    # ── Compare against every finished run ──────────────────
    stats = _load_stats()
    qtys   = stats["all_total_qtys"]
    spends = stats.get("all_total_spends", [])
    pct_rank       = round(len([q for q in qtys if q < total_qty]) / len(qtys) * 100) if qtys else None
    avg_qty        = round(sum(qtys) / len(qtys)) if qtys else None
    avg_spend      = round(sum(spends) / len(spends), 2) if spends else None
    total_all_spend = round(sum(spends), 2) if spends else None
    most_common_top = max(stats["top_items"], key=stats["top_items"].get) if stats["top_items"] else None

    # Semester label from stored metadata start date
    try:
        start_month = date.fromisoformat(meta["startdate"]).month
        start_year  = date.fromisoformat(meta["startdate"]).year
        semester_label = f"Spring {start_year}" if start_month <= 6 else f"Fall {start_year}"
    except Exception:
        semester_label = "This Semester"

    return render_template(
        "wrapped.html",
        items=top_items,
        metadata=meta,
        student_name=meta.get("name"),
        total_qty=total_qty,
        total_spend=total_spend,
        unique_items=unique_items,
        items_per_week=items_per_week,
        cfa_qty=cfa_qty,
        cfa_spend=cfa_spend,
        tacos_qty=tacos_qty,
        tacos_spend=tacos_spend,
        drjacks_qty=drjacks_qty,
        drjacks_spend=drjacks_spend,
        times=time_insights(transactions, meta["startdate"], meta["enddate"]),
        swipes=time_insights(result.get("swipes", []), meta["startdate"], meta["enddate"]),
        ai=result.get("analysis"),
        total_runs=stats["total_runs"],
        pct_rank=pct_rank,
        avg_qty=avg_qty,
        avg_spend=avg_spend,
        total_all_spend=total_all_spend,
        most_common_top=most_common_top,
        semester_label=semester_label,
        dashboard_url=url_for("results", share_id=share_id),
    )


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
