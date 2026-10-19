"""Time-based slide stats (earliest/latest, favorite spots by meal, busiest weekdays)
for any list of {date, location} events: Flex purchases or meal-plan swipes.

Atrium's statement shows each timestamp as display text, so dates are parsed
leniently. Events whose text has no recognizable date and time are left out of
these slides rather than guessed at.
"""
import re
from collections import Counter
from datetime import date, datetime, timedelta

_NUMERIC_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")
_NAMED_DATE = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?(?:\s+(\d{4}))?\b")
_TIME = re.compile(r"\b(\d{1,2}):(\d{2})(?::\d{2})?\s*([AaPp])\.?[Mm]?\b|\b(\d{1,2}):(\d{2})(?::\d{2})?\b")
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MEALS = (("Breakfast", 0, 10 * 60 + 30), ("Lunch", 10 * 60 + 30, 16 * 60), ("Dinner", 16 * 60, 24 * 60))
LATE_NIGHT_CUTOFF = 4 * 60  # purchases before 4 AM count as the previous night's latest
_REGISTER = re.compile(r"\s+(?:PAX\s+)?\d+$", re.IGNORECASE)
# What students call each spot. Swipes happen at the Caf and its grab-and-go, Tiger Express.
_SPOT_NAMES = {"Commons": "the Caf", "Tacos": "Tacos 4 Life", "Tiger Exp": "Tiger Express"}


def spot_name(location: str) -> str:
    """Atrium names each register ("CFA 2", "Commons PAX 4"); fold them into one spot."""
    spot = _REGISTER.sub("", location)
    return _SPOT_NAMES.get(spot, spot)


def parse_when(text: str, default_year: int) -> datetime | None:
    m = _NUMERIC_DATE.search(text)
    if m:
        month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        year += 2000 if year < 100 else 0
    else:
        # Skip weekday names like "Mon" until a month name matches.
        m = next((c for c in _NAMED_DATE.finditer(text) if c.group(1)[:3].lower() in _MONTHS), None)
        if not m:
            return None
        month, day, year = _MONTHS[m.group(1)[:3].lower()], int(m.group(2)), int(m.group(3) or default_year)

    t = _TIME.search(text[m.end():]) or _TIME.search(text)
    if not t:
        return None
    if t.group(1):
        hour, minute = int(t.group(1)) % 12, int(t.group(2))
        hour += 12 if t.group(3).lower() == "p" else 0
    else:
        hour, minute = int(t.group(4)), int(t.group(5))
    try:
        return datetime(year, month, day, hour, minute)
    except ValueError:
        return None


def _meal(minutes: int) -> str:
    return next(name for name, start, end in MEALS if start <= minutes < end)


def _weekday_counts(start: date, end: date) -> Counter:
    days = Counter()
    d = start
    while d <= end:
        days[d.weekday()] += 1
        d += timedelta(days=1)
    return days


def time_insights(events: list, startdate: str, enddate: str) -> dict | None:
    start, end = date.fromisoformat(startdate), date.fromisoformat(enddate)
    timed = [(parse_when(e["date"], start.year), spot_name(e["location"])) for e in events]
    timed = [(when, loc) for when, loc in timed if when]
    if not timed:
        return None

    def late_key(pair):
        minutes = pair[0].hour * 60 + pair[0].minute
        return minutes + (24 * 60 if minutes < LATE_NIGHT_CUTOFF else 0)

    earliest = min(timed, key=lambda p: (late_key(p), p[0]))
    latest = max(timed, key=lambda p: (late_key(p), p[0]))

    by_meal = {name: Counter() for name, _, _ in MEALS}
    for when, loc in timed:
        by_meal[_meal(when.hour * 60 + when.minute)][loc] += 1
    all_spots = Counter(loc for _, loc in timed)

    # Count weekdays from the first purchase, not the semester's nominal start
    # (Fall starts July 1, weeks before anyone is on campus).
    first = min(when for when, _ in timed).date()
    weekday_days = _weekday_counts(max(start, first), end)
    weekday_visits = Counter(when.weekday() for when, _ in timed)
    weekdays = [
        {"day": WEEKDAYS[i], "total": weekday_visits[i],
         "avg": round(weekday_visits[i] / weekday_days[i], 1) if weekday_days[i] else 0.0}
        for i in range(7)
    ]

    def moment(pair):
        when, loc = pair
        return {"time": when.strftime("%I:%M %p").lstrip("0"), "date": f"{when:%B} {when.day}, {when.year}", "location": loc}

    return {
        "count": len(timed),
        "meal_totals": [{"meal": meal, "count": sum(counts.values())} for meal, counts in by_meal.items()],
        "earliest": moment(earliest),
        "latest": moment(latest),
        "spots": [
            {"meal": meal, "top": [{"location": loc, "count": n} for loc, n in counts.most_common(3)]}
            for meal, counts in [*by_meal.items(), ("All visits", all_spots)]
            if counts
        ],
        "weekdays": weekdays,
    }
