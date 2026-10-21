#!/usr/bin/env python3
"""Backfill the canonical five-digit Atrium student ID and derived OBU email.

Dry-run by default. ``--apply`` first creates a timestamped backup under
``data/backups/``, then migrates archives, results, credentials, stats and analytics.
The full ID is parsed from each archived statement's Customer Information panel;
the masked ``Account Number: XX###`` is never used.
"""
import argparse
import glob
import json
import os
import re
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import analytics  # noqa: E402
import app  # noqa: E402
import mailbox  # noqa: E402
from scraper import student_identity  # noqa: E402


def _load(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)
        return value if isinstance(value, dict) else None
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write(path: str, value: dict) -> None:
    tmp = f"{path}.identity.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f)
    os.replace(tmp, path)


def _name_key(name: str | None) -> str:
    return " ".join((name or "").casefold().split())


def _json_files(directory: str) -> list[str]:
    return sorted(glob.glob(os.path.join(directory, "*.json")))


def _put(mapping: dict[str, dict], key: str | None, identity: dict, label: str,
         errors: list[str]) -> None:
    if not key:
        return
    previous = mapping.get(key)
    if previous and previous["student_id"] != identity["student_id"]:
        errors.append(f"{label} {key!r} maps to both {previous['student_id']} and {identity['student_id']}")
    else:
        mapping[key] = identity


def _build_plan() -> dict:
    errors: list[str] = []
    archive_updates: dict[str, dict] = {}
    result_updates: dict[str, dict] = {}
    credential_updates: dict[str, dict] = {}
    identities: dict[str, dict] = {}
    by_run: dict[str, dict] = {}
    by_tag: dict[str, dict] = {}
    by_old_key: dict[str, dict] = {}
    by_name: dict[str, list[dict]] = defaultdict(list)

    for path in _json_files(app.ARCHIVE_DIR):
        run = _load(path)
        if not run:
            errors.append(f"unreadable archive: {path}")
            continue
        try:
            identity = student_identity(run.get("pages") or [], run.get("name"))
        except ValueError as exc:
            errors.append(f"{os.path.basename(path)}: {exc}")
            continue
        student_id = identity["student_id"]
        previous = identities.get(student_id)
        if previous and previous["email"] != identity["email"]:
            errors.append(f"student {student_id} derives both {previous['email']} and {identity['email']}")
            continue
        identity = {**identity, "name": run.get("name")}
        identities[student_id] = identity
        _put(by_run, run.get("run_id"), identity, "run", errors)
        _put(by_tag, run.get("inbox"), identity, "inbox", errors)
        _put(by_old_key, run.get("student_key"), identity, "legacy key", errors)
        name_key = _name_key(run.get("name"))
        if name_key and all(item["student_id"] != student_id for item in by_name[name_key]):
            by_name[name_key].append(identity)
        migrated = {**run, "student_id": student_id, "email": identity["email"]}
        migrated.pop("student_key", None)
        if migrated != run:
            archive_updates[path] = migrated

    for path in _json_files(app.RESULTS_DIR):
        result = _load(path)
        if not result or not isinstance(result.get("metadata"), dict):
            errors.append(f"unreadable result: {path}")
            continue
        meta = result["metadata"]
        old_key = meta.get("student_key")
        current_id = meta.get("student_id")
        fake_id = current_id if str(current_id).startswith("test-") else old_key
        if str(fake_id).startswith("test-"):
            migrated = {**meta, "student_id": fake_id}
            migrated.pop("student_key", None)
            if migrated != meta:
                result_updates[path] = {**result, "metadata": migrated}
            continue
        candidates = [by_run.get(meta.get("run_id")), by_old_key.get(old_key),
                      identities.get(str(current_id))]
        names = by_name.get(_name_key(meta.get("name")), [])
        if len(names) == 1:
            candidates.append(names[0])
        found = {item["student_id"]: item for item in candidates if item}
        if len(found) != 1:
            errors.append(f"{os.path.basename(path)}: expected one student identity, found {len(found)}")
            continue
        identity = next(iter(found.values()))
        _put(by_old_key, old_key, identity, "legacy key", errors)
        migrated = {**meta, "student_id": identity["student_id"], "email": identity["email"]}
        migrated.pop("student_key", None)
        if migrated != meta:
            result_updates[path] = {**result, "metadata": migrated}

    for path in _json_files(mailbox.CREDENTIALS_DIR):
        invite = _load(path)
        if not invite:
            errors.append(f"unreadable credential: {path}")
            continue
        tag = os.path.basename(path)[:-5]
        identity = by_tag.get(tag)
        if not identity:
            names = by_name.get(_name_key(invite.get("name")), [])
            identity = names[0] if len(names) == 1 else None
        migrated = dict(invite)
        migrated.pop("student_key", None)
        if identity:
            migrated.update(student_id=identity["student_id"], email=identity["email"])
        if migrated != invite:
            credential_updates[path] = migrated

    stats = _load(app.STATS_FILE) or {}
    students = stats.get("students", {})
    if not isinstance(students, dict):
        errors.append("stats.json students value is not an object")
        students = {}
    migrated_students: dict[str, dict] = {}
    for key, summary in students.items():
        if str(key).startswith("test-") or re.fullmatch(r"\d{5}", str(key)):
            student_id = str(key)
        else:
            identity = by_old_key.get(str(key))
            if not identity:
                errors.append(f"stats key {key!r} has no student identity")
                continue
            student_id = identity["student_id"]
        previous = migrated_students.get(student_id)
        if not previous or str(summary.get("updated", "")) >= str(previous.get("updated", "")):
            migrated_students[student_id] = summary
    migrated_stats = {**stats, "students": migrated_students}

    event_students: set[str] = set()
    if os.path.exists(analytics.DB_FILE):
        con = sqlite3.connect(f"file:{analytics.DB_FILE}?mode=ro", uri=True)
        try:
            event_students = {str(row[0]) for row in con.execute(
                "SELECT DISTINCT student FROM events WHERE student IS NOT NULL"
            )}
        finally:
            con.close()
    unidentified_event_keys = {
        value for value in event_students
        if not re.fullmatch(r"\d{5}", value)
        and not value.startswith("test-")
        and value not in by_old_key
    }

    return {
        "errors": errors,
        "identities": identities,
        "by_tag": by_tag,
        "by_old_key": by_old_key,
        "unidentified_event_keys": unidentified_event_keys,
        "archives": archive_updates,
        "results": result_updates,
        "credentials": credential_updates,
        "stats": migrated_stats,
        "stats_changed": migrated_stats != stats,
    }


def _backup() -> str:
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    root = os.path.join(app.DATA_DIR, "backups", f"student-identity-{stamp}")
    os.makedirs(root, exist_ok=False)
    for label, source in (("archive", app.ARCHIVE_DIR), ("results", app.RESULTS_DIR),
                          ("credentials", mailbox.CREDENTIALS_DIR)):
        if os.path.isdir(source):
            shutil.copytree(source, os.path.join(root, label))
    if os.path.exists(app.STATS_FILE):
        shutil.copy2(app.STATS_FILE, os.path.join(root, "stats.json"))
    if os.path.exists(analytics.DB_FILE):
        source = sqlite3.connect(analytics.DB_FILE)
        destination = sqlite3.connect(os.path.join(root, "analytics.db"))
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
    return root


def _apply(plan: dict) -> str:
    backup = _backup()
    for group in ("archives", "results", "credentials"):
        for path, value in plan[group].items():
            _write(path, value)
    if plan["stats_changed"]:
        _write(app.STATS_FILE, plan["stats"])

    con = analytics.connect()
    try:
        with con:
            now = datetime.now().isoformat(timespec="seconds")
            for identity in plan["identities"].values():
                con.execute(
                    """INSERT INTO students (student_id, email, name, updated_at)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(student_id) DO UPDATE SET
                         email = excluded.email, name = excluded.name, updated_at = excluded.updated_at""",
                    (identity["student_id"], identity["email"], identity.get("name"), now),
                )
            for old_key, identity in plan["by_old_key"].items():
                if old_key != identity["student_id"]:
                    con.execute("UPDATE events SET student = ? WHERE student = ?",
                                (identity["student_id"], old_key))
            for old_key in plan["unidentified_event_keys"]:
                con.execute("UPDATE events SET student = NULL WHERE student = ?", (old_key,))
            for tag, identity in plan["by_tag"].items():
                con.execute("UPDATE events SET student = ? WHERE visitor = ? AND student IS NULL",
                            (identity["student_id"], analytics.visitor_id(tag)))
    finally:
        con.close()
    return backup


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write the migration after making a backup")
    args = parser.parse_args()
    plan = _build_plan()
    print(f"students={len(plan['identities'])} archives={len(plan['archives'])} "
          f"results={len(plan['results'])} credentials={len(plan['credentials'])} "
          f"stats={'change' if plan['stats_changed'] else 'unchanged'} "
          f"unidentified_event_keys={len(plan['unidentified_event_keys'])}")
    if plan["errors"]:
        for error in plan["errors"]:
            print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(f"refusing migration with {len(plan['errors'])} error(s)")
    if not args.apply:
        print("dry run only; run again with --apply")
        return
    backup = _apply(plan)
    print(f"migration applied; backup={backup}")


if __name__ == "__main__":
    main()
