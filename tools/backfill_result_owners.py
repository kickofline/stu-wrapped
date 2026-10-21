#!/usr/bin/env python3
"""Give Wrappeds saved before owner tokens an owner: the inbox that produced them.

Ownership used to be "the mine cookie equals the share id", which anyone holding a share
link could forge. Each archived run records its inbox tag, so a saved result whose
run_id appears in the archive gets owner_visitor = analytics.visitor_id(inbox): the
browser holding that inbox cookie owns it. Results with no archived run keep no owner
(raw receipts, dashboard and CSV stay closed until the student generates again).

Dry run by default; --apply backs up data/results first. Idempotent.
"""
import argparse
import glob
import json
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import analytics  # noqa: E402
import app  # noqa: E402
from mailbox import is_tag  # noqa: E402


def _load(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def plan() -> tuple[dict[str, dict], int]:
    """({result path: updated result}, results left without an owner)."""
    inbox_by_run = {}
    for path in glob.glob(os.path.join(app.ARCHIVE_DIR, "*.json")):
        run = _load(path) or {}
        if run.get("run_id") and is_tag(run.get("inbox") or ""):
            inbox_by_run[run["run_id"]] = run["inbox"]
    updates, orphans = {}, 0
    for path in sorted(glob.glob(os.path.join(app.RESULTS_DIR, "*.json"))):
        result = _load(path)
        meta = (result or {}).get("metadata")
        if not isinstance(meta, dict) or meta.get("owner_visitor"):
            continue
        inbox = inbox_by_run.get(meta.get("run_id"))
        if not inbox:
            orphans += 1
            continue
        updates[path] = {**result, "metadata": {**meta, "owner_visitor": analytics.visitor_id(inbox),
                                                "owner_hashes": meta.get("owner_hashes") or []}}
    return updates, orphans


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write owners after backing up data/results")
    args = parser.parse_args()
    updates, orphans = plan()
    print(f"results to update={len(updates)} without an archived run={orphans}")
    if not args.apply:
        print("dry run only; run again with --apply")
        return
    backup = os.path.join(app.DATA_DIR, "backups",
                          f"result-owners-{datetime.now():%Y%m%dT%H%M%S}")
    shutil.copytree(app.RESULTS_DIR, backup)
    for path, value in updates.items():
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(value, f)
        os.replace(path + ".tmp", path)
    print(f"updated {len(updates)}; backup={backup}")


if __name__ == "__main__":
    main()
