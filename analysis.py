"""DeepSeek (via skynet's LiteLLM) passes over a finished run.

1. normalize(): Atrium lists every register line separately with abbreviated
   names — a Dr. Jack's drink arrives as "(M) - Matcha Tea", "Sub Almond Milk",
   "Add Flavor Syrup". The model folds add-ons into the drink they modify and
   names each item the way the menu does (menus.md). Results are cached by the
   exact register lines, so the same order is only ever normalized once.
2. analyze(): a dining personality, a roast, and money observations written
   from the aggregated stats (no raw statement data is sent).

Both are optional garnish: if the model is unreachable or answers nonsense,
the run still finishes with the raw item names and without the AI slides.
"""
import json
import os
import re
import threading

import requests

BASE_URL = os.environ.get("LITELLM_BASE_URL", "")
API_KEY = os.environ.get("LITELLM_API_KEY", "")
MODEL = os.environ.get("LITELLM_MODEL", "deepseek-v4.1-flash")
TIMEOUT = 120

MENUS_FILE = os.path.join(os.path.dirname(__file__), "menus.md")
CACHE_FILE = os.path.join(os.path.dirname(__file__), "data", "normalize_cache.json")
_cache_lock = threading.Lock()


def enabled() -> bool:
    return bool(BASE_URL and API_KEY)


def _chat(system: str, user: str, max_tokens: int) -> dict:
    resp = requests.post(
        f"{BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={
            "model": MODEL,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "temperature": 0.4,
            "reasoning_effort": "none",
            "chat_template_kwargs": {"enable_thinking": False},
        },
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"] or ""
    match = re.search(r"\{.*\}", text, re.S)  # tolerate ```json fences
    if not match:
        raise ValueError("model reply had no JSON object")
    return json.loads(match.group(0))


# ── Item normalization ───────────────────────────────────────────────────────

NORMALIZE_SYSTEM = """You clean up college dining register receipts.
Each receipt is one purchase: a location and its register lines (quantity x name $line total).
Lines are abbreviated, and add-ons are separate lines that belong to the item before them:
milk swaps, syrups, extra shots, sauces, "Add ..." lines, combo upsizes, and included $0 sides or drinks.

For every receipt, return the real items the student got:
- Fold add-on lines into the item they modify; add their price to that item's total.
- "item": the menu name without size or add-ons, e.g. "Matcha Latte", "Chick-fil-A Nuggets (8 ct) Meal", "Fried Chicken Taco".
- "detail": size and add-ons in a few words, e.g. "Large, almond milk, flavor syrup"; "" if none.
- "qty": how many of that item; "total": dollars for that item including its add-ons.
- The totals of a receipt must add up to the receipt's line totals exactly.
Use the menus below for real names. Answer with JSON only:
{"receipts": [{"id": <receipt id>, "items": [{"item": str, "detail": str, "qty": int, "total": number}]}]}"""


def _menus() -> str:
    try:
        with open(MENUS_FILE, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "(no menu reference available)"


def _signature(tx: dict) -> str:
    location = re.sub(r"\s+\d+$", "", tx["location"])
    lines = "; ".join(f'{i["qty"]} x {i["name"]} ${(i.get("subtotal") or 0):.2f}' for i in tx["items"])
    return f"{location} | {lines}"


def _receipt_total(tx: dict) -> float:
    return round(sum(i.get("subtotal") or 0 for i in tx["items"]), 2)


def _valid(items, tx: dict) -> bool:
    try:
        ok = all(isinstance(i["item"], str) and i["item"].strip() and int(i["qty"]) > 0 for i in items)
        return ok and abs(sum(float(i["total"]) for i in items) - _receipt_total(tx)) <= 0.02
    except (KeyError, TypeError, ValueError):
        return False


def _load_cache() -> dict:
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def normalize(transactions: list) -> None:
    """Attach tx["clean_items"] to every transaction the model (or cache) could clean."""
    with _cache_lock:
        cache = _load_cache()
    pending = {}
    for tx in transactions:
        sig = _signature(tx)
        if sig in cache:
            tx["clean_items"] = cache[sig]
        else:
            pending.setdefault(sig, []).append(tx)

    sigs = list(pending)
    for start in range(0, len(sigs), 60):
        batch = sigs[start:start + 60]
        receipts = "\n".join(f"#{n} {sig}" for n, sig in enumerate(batch))
        reply = _chat(NORMALIZE_SYSTEM + "\n\nMENUS:\n" + _menus(), receipts, max_tokens=8000)
        for receipt in reply.get("receipts", []):
            try:
                sig = batch[int(receipt["id"])]
            except (KeyError, ValueError, IndexError, TypeError):
                continue
            items = receipt.get("items") or []
            if not _valid(items, pending[sig][0]):
                continue
            clean = [{"item": i["item"].strip(), "detail": (i.get("detail") or "").strip(),
                      "qty": int(i["qty"]), "total": round(float(i["total"]), 2)} for i in items]
            cache[sig] = clean
            for tx in pending[sig]:
                tx["clean_items"] = clean

    with _cache_lock:
        merged = {**_load_cache(), **cache}
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=1)
        os.replace(CACHE_FILE + ".tmp", CACHE_FILE)


# ── Personality / roast / money ──────────────────────────────────────────────

ANALYZE_SYSTEM = """You write the fun slides of "OBU Stu Wrapped", a Spotify-Wrapped-style recap
of one Ouachita Baptist University student's campus dining purchases this semester.
You get their stats as JSON. Write in second person, specific to their numbers and items, upbeat,
short, and clean (PG, no profanity, nothing about body or weight). Every claim must follow from
the numbers given: never invent comparisons, counts, or items, and check any "more than" against the data.
Answer with JSON only:
{"personality": {"title": "a 2-4 word dining persona", "description": "2-3 sentences"},
 "roast": "2-3 sentences of playful teasing about their habits",
 "money": ["3 short plain-language observations about their spending, the last one a gentle suggestion"]}"""


def analyze(stats: dict) -> dict | None:
    reply = _chat(ANALYZE_SYSTEM, json.dumps(stats), max_tokens=1200)
    personality = reply.get("personality") or {}
    money = [m for m in reply.get("money") or [] if isinstance(m, str) and m.strip()]
    if not (personality.get("title") and personality.get("description") and reply.get("roast") and money):
        return None
    return {
        "personality": {"title": personality["title"].strip(), "description": personality["description"].strip()},
        "roast": reply["roast"].strip(),
        "money": [m.strip() for m in money[:3]],
    }
