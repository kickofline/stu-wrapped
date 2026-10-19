"""Stu Wrapped usage report, printed to the terminal.

    python tools/report.py              # full report
    python tools/report.py --backfill   # once: load past signups and runs into the log

Reads data/analytics.db plus the saved results, scrape archive and invites, and lists
every student by name. Run it inside the app container:

    docker exec <container> /app/.venv/bin/python tools/report.py
"""
import glob
import json
import os
import sys
from collections import Counter, defaultdict
from contextlib import closing
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import analytics  # noqa: E402
import app  # noqa: E402
import mailbox  # noqa: E402

SITE = "https://stu.exposed"
SHARES = ("share_slide", "share_link", "compare_shared")


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _results():
    """share_id -> metadata for every saved Wrapped."""
    out = {}
    for path in glob.glob(os.path.join(app.RESULTS_DIR, "*.json")):
        data = _load_json(path)
        if data:
            out[os.path.basename(path)[:-5]] = data["metadata"]
    return out


def _archive():
    for path in sorted(glob.glob(os.path.join(app.ARCHIVE_DIR, "*.json"))):
        data = _load_json(path)
        if data:
            yield data


def _invites():
    """(tag, invite) for every saved Flex guest login."""
    for path in glob.glob(os.path.join(mailbox.CREDENTIALS_DIR, "*.json")):
        tag = os.path.basename(path)[:-5]
        data = _load_json(path)
        if data and mailbox.is_tag(tag):
            yield tag, data


# ── Backfill ─────────────────────────────────────────────────────────────────

def backfill() -> None:
    """Load signups and runs from before live tracking. Only history older than the first
    live event is imported, so nothing the app already recorded is counted twice."""
    with closing(analytics.connect()) as con:
        if con.execute("SELECT 1 FROM events WHERE json_extract(props, '$.backfill') LIMIT 1").fetchone():
            sys.exit("already backfilled")
        live_since = con.execute("SELECT MIN(ts) FROM events").fetchone()[0] or "9999"
    results = _results()
    by_run = {meta.get("run_id"): (sid, meta) for sid, meta in results.items()}
    added = Counter()

    for tag, invite in _invites():
        if (invite.get("received_at") or "9999") >= live_since:
            continue
        analytics.record("invite_received", ts=invite.get("received_at"), visitor=analytics.visitor_id(tag),
                         student=app._student_key(invite, tag), name=invite.get("name"), backfill=True)
        added["invite_received"] += 1

    seen_runs = set()
    for run in _archive():
        ts = datetime.strptime(run["archived_at"], "%Y%m%dT%H%M%S").isoformat(timespec="seconds")
        seen_runs.add(run["run_id"])
        if ts >= live_since:
            continue
        visitor = analytics.visitor_id(run.get("inbox"))
        common = {"ts": ts, "visitor": visitor, "student": run.get("student_key"), "name": run.get("name"),
                  "backfill": True}
        analytics.record("wrap_started", **common)
        if not run.get("purchases") and not run.get("swipes"):
            analytics.record("wrap_failed", reason="no_activity", **common)
            added["wrap_failed"] += 1
            continue
        sid, meta = by_run.get(run["run_id"], (None, None))
        analytics.record("wrap_finished", share_id=sid, purchases=len(run.get("purchases") or []),
                         swipes=len(run.get("swipes") or []), **common)
        added["wrap_finished"] += 1

    for sid, meta in results.items():  # runs saved before the archive existed
        if (meta.get("run_id") in seen_runs or str(meta.get("student_key", "")).startswith("test-")
                or (meta.get("generated_at") or "9999") >= live_since):
            continue
        analytics.record("wrap_finished", ts=meta.get("generated_at"), student=meta.get("student_key"),
                         share_id=sid, name=meta.get("name"), backfill=True)
        added["wrap_finished (results only)"] += 1
    print("backfilled:", dict(added))


# ── Report ───────────────────────────────────────────────────────────────────

def _events():
    with closing(analytics.connect()) as con:
        rows = con.execute("SELECT ts, name, visitor, student, share_id, props FROM events ORDER BY ts").fetchall()
    events = []
    for row in rows:
        e = dict(row)
        e["props"] = json.loads(e["props"] or "{}")
        e["day"] = e["ts"][:10]
        if str(e["student"] or "").startswith("test-"):
            continue
        events.append(e)
    return events


def _table(headers, rows):
    rows = [[str(c) for c in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
    line = lambda cells: "  ".join(c.ljust(w) for c, w in zip(cells, widths))
    print("  " + line(headers))
    print("  " + "  ".join("-" * w for w in widths))
    for r in rows:
        print("  " + line(r))


def _pct(part, whole):
    return f"{part / whole * 100:.0f}%" if whole else "-"


def report() -> None:
    events = _events()
    today = date.today().isoformat()
    week = (date.today() - timedelta(days=6)).isoformat()
    by_name = defaultdict(list)
    for e in events:
        by_name[e["name"]].append(e)

    def count(name, since=None, unique=None, where=lambda e: True):
        rows = [e for e in by_name[name] if (since is None or e["day"] >= since) and where(e)]
        return len({e[unique] for e in rows if e[unique]}) if unique else len(rows)

    print(f"STU WRAPPED REPORT - {datetime.now():%Y-%m-%d %H:%M}")
    first = events[0]["ts"][:10] if events else "-"
    print(f"{len(events)} events since {first}. Live tracking started with the analytics deploy; "
          "earlier signups and runs are backfilled.\n")

    friend_view = lambda e: not e["props"].get("owner")
    lines = [
        ("Landing visitors (unique)", dict(name="landing_view", unique="visitor")),
        ("Signups (students with an invite)", dict(name="invite_received", unique="student")),
        ("Wraps finished", dict(name="wrap_finished")),
        ("Wraps failed", dict(name="wrap_failed")),
        ("Wrapped views by friends", dict(name="wrapped_view", where=friend_view)),
        ("Slide shares", dict(name="share_slide", where=lambda e: e["props"].get("method") != "dismissed")),
        ("Link shares", dict(name="share_link", where=lambda e: e["props"].get("method") != "dismissed")),
        ("Compare shares", dict(name="compare_shared", where=lambda e: e["props"].get("method") != "dismissed")),
        ("Link previews (og image)", dict(name="link_preview")),
        ("Compare views", dict(name="compare_view")),
        ("Campus Leaderboard views", dict(name="campus_view")),
    ]
    print("TOTALS")
    _table(["", "today", "7 days", "all time"],
           [[label, count(since=today, **kw), count(since=week, **kw), count(**kw)] for label, kw in lines])

    print("\nFUNNEL (unique browsers, since live tracking)")
    landed = {e["visitor"] for e in by_name["landing_view"] if e["visitor"]}
    steps = [
        ("Landed", landed),
        ("Copied guest email", {e["visitor"] for e in by_name["email_copied"]} & landed),
        ("Invite received", {e["visitor"] for e in by_name["invite_received"]} & landed),
        ("Wrap finished", {e["visitor"] for e in by_name["wrap_finished"]} & landed),
        ("Shared", {e["visitor"] for n in SHARES for e in by_name[n]
                    if e["props"].get("method") != "dismissed"} & landed),
    ]
    _table(["step", "browsers", "of landed", "of previous"],
           [[label, len(s), _pct(len(s), len(landed)), _pct(len(s), len(steps[i - 1][1])) if i else "-"]
            for i, (label, s) in enumerate(steps)])

    print("\nWHY RUNS FAILED")
    reasons = Counter(e["props"].get("reason") for e in by_name["wrap_failed"])
    rejected = Counter(e["props"].get("reason") for e in by_name["invite_rejected"])
    _table(["reason", "count"], [[r, n] for r, n in reasons.most_common()]
           + [[f"invite rejected: {r}", n] for r, n in rejected.most_common()])

    print("\nSHARES")
    methods = Counter((e["name"], e["props"].get("method")) for n in SHARES for e in by_name[n])
    _table(["event", "method", "count"], [[n, m, c] for (n, m), c in sorted(methods.items())])
    slides = Counter(e["props"].get("slide") for e in by_name["share_slide"] if e["props"].get("method") != "dismissed")
    if slides:
        print()
        _table(["slide shared", "count"], slides.most_common())
    previews = Counter(e["props"].get("app") for e in by_name["link_preview"])
    if previews:
        print()
        _table(["link previewed in", "count"], previews.most_common())

    print("\nDECK COMPLETION (furthest slide per browser and Wrapped)")
    furthest = {}
    for e in by_name["slide_reached"]:
        key = (e["visitor"], e["share_id"])
        if e["props"].get("index", 0) >= furthest.get(key, {}).get("index", 0):
            furthest[key] = e["props"]
    finished = sum(1 for p in furthest.values() if p.get("index") == p.get("total"))
    print(f"  {len(furthest)} visits reported, {finished} reached the last slide ({_pct(finished, len(furthest))})")
    stops = Counter(p.get("slide") for p in furthest.values() if p.get("index") != p.get("total"))
    if stops:
        _table(["stopped at", "visits"], stops.most_common(8))

    print("\nGROWTH FROM SHARING")
    friend_landed = {e["visitor"] for e in by_name["landing_view"] if e["props"].get("from_friend")}
    friend_signed = friend_landed & {e["visitor"] for e in by_name["invite_received"]}
    friend_wrapped = friend_landed & {e["visitor"] for e in by_name["wrap_finished"]}
    viewers = {e["visitor"] for e in by_name["wrapped_view"] if friend_view(e) and e["visitor"]}
    _table(["", "browsers"], [
        ["Viewed a friend's Wrapped", len(viewers)],
        ["Landed from a friend's link", len(friend_landed)],
        ["...then signed up", len(friend_signed)],
        ["...then finished a Wrapped", len(friend_wrapped)],
    ])

    print("\nLAST 14 DAYS")
    days = [(date.today() - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
    _table(["day", "visitors", "signups", "wraps", "failed", "shares", "friend views"], [[
        d,
        len({e["visitor"] for e in by_name["landing_view"] if e["day"] == d}),
        len({e["student"] for e in by_name["invite_received"] if e["day"] == d}),
        sum(e["day"] == d for e in by_name["wrap_finished"]),
        sum(e["day"] == d for e in by_name["wrap_failed"]),
        sum(e["day"] == d and e["props"].get("method") != "dismissed" for n in SHARES for e in by_name[n]),
        sum(e["day"] == d and friend_view(e) for e in by_name["wrapped_view"]),
    ] for d in days])

    _users(events)


def _users(events) -> None:
    """Every student by name, from invites, runs, saved results and the event log."""
    users = defaultdict(lambda: {"names": Counter(), "first": None, "last": None, "shares": set(),
                                 "invites": 0, "wraps": 0, "failed": 0, "friend_views": 0,
                                 "shared": 0, "previews": 0, "compares": 0, "latest": None})

    def touch(key, ts, name=None):
        u = users[key]
        if name:
            u["names"][name] += 1
        if ts:
            u["first"] = min(filter(None, [u["first"], ts]))
            u["last"] = max(filter(None, [u["last"], ts]))
        return u

    for tag, invite in _invites():
        touch(app._student_key(invite, tag), invite.get("received_at"), invite.get("name"))
    for sid, meta in _results().items():
        key = meta.get("student_key")
        if not key or str(key).startswith("test-"):
            continue
        u = touch(key, meta.get("generated_at"), meta.get("name"))
        u["shares"].add(sid)
        if not u["latest"] or meta.get("generated_at", "") > u["latest"][1]:
            u["latest"] = (sid, meta.get("generated_at", ""))

    for e in events:
        key = e["student"]
        if not key:
            continue
        u = touch(key, e["ts"], e["props"].get("name"))
        name = e["name"]
        if name == "invite_received":
            u["invites"] += 1
        elif name == "wrap_finished":
            u["wraps"] += 1
        elif name == "wrap_failed":
            u["failed"] += 1
        elif name == "wrapped_view" and not e["props"].get("owner"):
            u["friend_views"] += 1
        elif name in SHARES and e["props"].get("method") != "dismissed":
            u["shared"] += 1
        elif name == "link_preview":
            u["previews"] += 1
        elif name == "compare_view":
            u["compares"] += 1

    print(f"\nUSERS ({len(users)})")
    rows = []
    for key, u in sorted(users.items(), key=lambda kv: kv[1]["first"] or "", reverse=True):
        name = u["names"].most_common(1)[0][0] if u["names"] else "(no name)"
        link = f"{SITE}/wrapped/{u['latest'][0]}" if u["latest"] else "-"
        rows.append([name, (u["first"] or "-")[:16].replace("T", " "), (u["last"] or "-")[:16].replace("T", " "),
                     u["invites"], u["wraps"], u["failed"], u["shared"], u["friend_views"], u["previews"],
                     u["compares"], link])
    _table(["name", "first seen", "last seen", "invites", "wraps", "failed", "shares", "friend views",
            "previews", "compares", "latest Wrapped"], rows)


if __name__ == "__main__":
    backfill() if "--backfill" in sys.argv else report()
