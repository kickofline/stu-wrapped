import json
import os
import secrets
import threading
from collections import defaultdict
from datetime import date, datetime


from dotenv import load_dotenv
from flask import (Flask, Response, flash, jsonify, redirect,
                   render_template, request, url_for)

load_dotenv()

from email_watcher import make_plus_address
from jobs import cleanup_old_jobs, create_job, get_job
from name_map import expand_name
from scraper import run_scrape_job

app = Flask(__name__)
app.secret_key = "LUKERMKNGAONMXXMGJYAAGYMRIVVECNV"

STATS_FILE     = os.path.join(os.path.dirname(__file__), "data", "stats.json")
RAW_ITEMS_FILE = os.path.join(os.path.dirname(__file__), "data", "raw_items.json")


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
    """Aggregate per-item stats from raw transaction list, sorted by qty desc."""
    stats: dict = defaultdict(lambda: {"qty": 0, "total_spend": 0.0, "unit_price": None})
    for tx in transactions:
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


def _semester_dates() -> tuple[str, str, str]:
    today = date.today()
    if today.month <= 6:
        start = date(today.year, 1, 1)
        label = f"Spring {today.year}"
    else:
        start = date(today.year, 7, 1)
        label = f"Fall {today.year}"
    return start.isoformat(), today.isoformat(), label


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/address")
def address():
    sid = request.args.get("student_id", "").strip()
    if not sid.isdigit() or len(sid) < 4:
        return jsonify({"error": "Enter your numeric OBU student ID"}), 400
    return jsonify({"plus_address": make_plus_address(sid)})


@app.post("/start")
def start():
    cleanup_old_jobs()

    sid = request.form.get("student_id", "").strip()
    if not sid.isdigit() or len(sid) < 4:
        flash("Please enter a valid OBU student ID.", "error")
        return redirect(url_for("index"))

    startdate, enddate, _ = _semester_dates()
    acct = "1"

    plus_address = make_plus_address(sid)
    job_id = create_job(total_steps=10, plus_address=plus_address, job_id=secrets.token_hex(4))
    print(f"[app] /start created job={job_id} plus_address={plus_address}", flush=True)

    thread = threading.Thread(
        target=run_scrape_job,
        args=(job_id, startdate, enddate, plus_address, acct),
        daemon=True,
    )
    thread.start()

    return redirect(url_for("wait", job_id=job_id))


@app.get("/wait/<job_id>")
def wait(job_id):
    job = get_job(job_id)
    print(f"[app] /wait job={job_id} found={job is not None}", flush=True)
    if not job:
        flash("Job not found.", "error")
        return redirect(url_for("index"))
    return render_template("waiting.html", job=job)


@app.get("/status/<job_id>")
def status(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"status": "error", "message": "Job not found"}), 404

    resp = {
        "status": job["status"],
        "step": job["step"],
        "total_steps": job["total_steps"],
        "message": job["message"],
        "redirect": None,
        "error": job.get("error"),
    }
    if job["status"] == "done":
        resp["redirect"] = url_for("wrapped", job_id=job_id)

    return jsonify(resp)


@app.get("/results/<job_id>")
def results(job_id):
    job = get_job(job_id)
    if not job or job["status"] != "done":
        flash("Results not available.", "error")
        return redirect(url_for("index"))

    transactions = job["result"]
    items = _aggregate_items(transactions)
    total_qty = sum(i["qty"] for i in items)
    total_spend = round(sum(i["total_spend"] for i in items), 2)

    return render_template(
        "dashboard.html",
        items=items,
        items_json=json.dumps(items),
        metadata=job["metadata"],
        total_qty=total_qty,
        total_spend=total_spend,
        unique_items=len(items),
        job_id=job_id,
    )


@app.get("/wrapped/<job_id>")
def wrapped(job_id):
    job = get_job(job_id)
    if not job or job["status"] != "done":
        flash("Results not available.", "error")
        return redirect(url_for("index"))

    transactions = job["result"]
    meta = job["metadata"]
    _record_raw_items(transactions)
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

    # ── Persist & compare ────────────────────────────────────
    stats = _load_stats()
    stats["total_runs"] += 1
    stats["all_total_qtys"].append(total_qty)
    stats.setdefault("all_total_spends", []).append(total_spend)
    stats["all_unique_items"].append(unique_items)
    top_name = top_items[0]["name"] if top_items else None
    if top_name:
        stats["top_items"][top_name] = stats["top_items"].get(top_name, 0) + 1
    _save_stats(stats)

    # Percentile among all runs (including this one)
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
        total_runs=stats["total_runs"],
        pct_rank=pct_rank,
        avg_qty=avg_qty,
        avg_spend=avg_spend,
        total_all_spend=total_all_spend,
        most_common_top=most_common_top,
        semester_label=semester_label,
        dashboard_url=url_for("results", job_id=job_id),
    )


@app.get("/api/results/<job_id>")
def api_results(job_id):
    job = get_job(job_id)
    if not job or job["status"] != "done":
        return jsonify({"error": "not found"}), 404
    return jsonify({"transactions": job["result"], "metadata": job["metadata"]})


@app.get("/download/<job_id>/csv")
def download_csv(job_id):
    job = get_job(job_id)
    if not job or job["status"] != "done":
        return "Not found", 404

    items = _aggregate_items(job["result"])
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
