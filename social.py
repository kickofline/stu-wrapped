"""Deterministic social insights for Wrapped slides and friend comparisons."""
import re
from collections import Counter
from datetime import date, timedelta

from insights import WEEKDAYS, flex_paid, parse_when, spot_name
from name_map import expand_name


def _transaction_items(transaction: dict) -> list[dict]:
    """Return one receipt's real items in display order, with duplicate lines combined."""
    source = transaction.get("clean_items")
    if source is None:
        source = [
            {
                "item": expand_name(item.get("name", "")),
                "detail": "",
                "qty": item.get("qty", 0),
                "total": item.get("subtotal") or 0,
            }
            for item in transaction.get("items", [])
            if item.get("unit_price")
        ]

    combined = {}
    for item in source:
        name = (item.get("item") or "").strip()
        detail = (item.get("detail") or "").strip()
        qty = int(item.get("qty") or 0)
        if not name or qty <= 0:
            continue
        key = (name.casefold(), detail.casefold())
        if key not in combined:
            combined[key] = {"name": name, "detail": detail, "qty": 0, "total": 0.0}
        combined[key]["qty"] += qty
        combined[key]["total"] += float(item.get("total") or 0)

    return [
        {**item, "total": round(item["total"], 2)}
        for item in combined.values()
    ]


def item_quantities(transactions: list) -> dict[str, int]:
    quantities = Counter()
    names = {}
    for transaction in transactions:
        for item in _transaction_items(transaction):
            key = item["name"].casefold()
            names.setdefault(key, item["name"])
            quantities[key] += item["qty"]
    return {names[key]: qty for key, qty in quantities.items()}


def signature_order(transactions: list) -> dict | None:
    """The exact item/detail/quantity basket repeated most often; ties favor spend."""
    baskets = {}
    for transaction in transactions:
        items = _transaction_items(transaction)
        if not items:
            continue
        key = tuple(sorted(
            (item["name"].casefold(), item["detail"].casefold(), item["qty"])
            for item in items
        ))
        basket = baskets.setdefault(key, {"count": 0, "spent": 0.0, "items": items})
        basket["count"] += 1
        basket["spent"] += float(transaction.get("grand_total") or 0)

    if not baskets:
        return None
    winner = min(baskets.values(), key=lambda b: (-b["count"], -b["spent"], str(b["items"])))
    if winner["count"] < 2:
        return None
    return {
        "count": winner["count"],
        "spent": round(winner["spent"], 2),
        "average": round(winner["spent"] / winner["count"], 2),
        "items": winner["items"],
    }


def summary_highlights(transactions: list, swipes: list, metadata: dict) -> dict:
    """Compact, non-ranking facts for the final share card."""
    year = date.fromisoformat(metadata["startdate"]).year
    parsed_transactions = [
        (when, transaction)
        for transaction in transactions
        if (when := parse_when(transaction.get("date", ""), year))
    ]

    day_spend = Counter()
    day_visits = Counter()
    for when, transaction in parsed_transactions:
        day_spend[when.date()] += float(transaction.get("grand_total") or 0)
        day_visits[when.date()] += 1
    biggest_day = None
    if day_spend:
        day = max(day_spend, key=lambda value: (day_spend[value], value))
        biggest_day = {
            "date": f"{day:%b} {day.day}",
            "amount": round(day_spend[day], 2),
            "visits": day_visits[day],
        }

    biggest_order = None
    if transactions:
        transaction = max(
            transactions,
            key=lambda value: (float(value.get("grand_total") or 0), value.get("date", "")),
        )
        order_items = _transaction_items(transaction)
        biggest_order = {
            "amount": round(float(transaction.get("grand_total") or 0), 2),
            "spot": spot_name(transaction.get("location", "")),
            "item": order_items[0]["name"] if order_items else "Flex order",
            "more_items": max(0, len(order_items) - 1),
        }

    moments = [when for when, _ in parsed_transactions]
    moments.extend(
        when for swipe in swipes
        if (when := parse_when(swipe.get("date", ""), year))
    )
    routine = None
    if moments:
        weekdays = Counter(when.weekday() for when in moments)
        weekday = max(range(7), key=lambda value: (weekdays[value], -value))
        meals = Counter(
            "Breakfast" if when.hour * 60 + when.minute < 10 * 60 + 30
            else "Lunch" if when.hour < 16
            else "Dinner"
            for when in moments
        )
        meal_order = ("Breakfast", "Lunch", "Dinner")
        meal = max(meal_order, key=lambda value: (meals[value], -meal_order.index(value)))
        routine = {
            "weekday": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")[weekday],
            "weekday_visits": weekdays[weekday],
            "meal": meal,
            "meal_visits": meals[meal],
            "late_night": sum(when.hour >= 20 or when.hour < 4 for when in moments),
        }

    return {
        "biggest_day": biggest_day,
        "biggest_order": biggest_order,
        "routine": routine,
        "caf_swipes": len(swipes),
        "flex_visits": len(transactions),
        "flex_spend": round(sum(flex_paid(transaction) for transaction in transactions), 2),
    }


COFFEE_WORDS = ("latte", "macchiato", "mocha", "coffee", "london fog", "frapjack", "americano",
                "cappuccino", "cold brew", "espresso", "chai")
ENERGY_WORDS = ("alani", "celsius", "red bull", "bloom", "energy", "monster")
DESSERT_WORDS = ("cookie", "shake", "brownie", "ice cream", "frosted", "sopapilla", "donut", "cake",
                 "gummies", "sour patch")
# A Flex purchase this soon after a Caf swipe is a second meal on top of the Caf one.
SECOND_DINNER_WINDOW = timedelta(minutes=90)
_SIZE = re.compile(r"^(?:\d+\s*oz|small|medium|large|meal)$", re.IGNORECASE)


def _count_matching(transactions: list, words: tuple, skip: tuple = ()) -> tuple[int, int]:
    """(how many, how many different kinds) of the items whose name has any of `words` and
    none of `skip`. Kinds are name plus flavor, ignoring sizes ("Alani Nu, Cotton Candy")."""
    count, kinds = 0, set()
    for transaction in transactions:
        for item in _transaction_items(transaction):
            name = item["name"].casefold()
            if any(w in name for w in words) and not any(w in name for w in skip):
                count += item["qty"]
                flavor = ", ".join(p.strip() for p in item["detail"].casefold().split(",")
                                   if p.strip() and not _SIZE.match(p.strip()))
                kinds.add((name, flavor))
    return count, len(kinds)


def nuggets_eaten(transactions: list) -> int:
    """Nuggets across every nugget order: the count in the name ("(12 ct)") times quantity.
    Chick-n-Minis are sandwiches, so they don't count."""
    total = 0
    for transaction in transactions:
        for item in _transaction_items(transaction):
            counted = re.search(r"Nuggets \((\d+) ct\)", item["name"])
            if counted:
                total += int(counted.group(1)) * item["qty"]
    return total


def roast_facts(transactions: list, swipes: list, metadata: dict) -> dict:
    """Extra numbers for the AI's roast: Flex spent in the first week of buying, the longest
    break between Caf swipes, and nuggets eaten."""
    year = date.fromisoformat(metadata["startdate"]).year
    bought = sorted(((when, t) for t in transactions if (when := parse_when(t.get("date", ""), year))),
                    key=lambda pair: pair[0])
    first_week = None
    if bought:
        start = bought[0][0].date()
        first_week = round(sum(flex_paid(t) for when, t in bought if (when.date() - start).days < 7), 2)
    caf_days = sorted(_event_days(swipes, year))
    longest_break = max(((b - a).days - 1 for a, b in zip(caf_days, caf_days[1:])), default=None)
    return {
        "flex_spent_first_week": first_week,
        "longest_days_without_caf": longest_break,
        "nuggets_eaten": nuggets_eaten(transactions),
    }


def dining_badges(transactions: list, swipes: list, metadata: dict) -> list[dict]:
    """Return the three strongest rule-based badges, in stable order. Thresholds are set
    from real students so each badge marks out a habit instead of going to everyone."""
    year = date.fromisoformat(metadata["startdate"]).year
    timed = [
        (when, spot_name(transaction.get("location", "")))
        for transaction in transactions
        if (when := parse_when(transaction.get("date", ""), year))
    ]
    caf_times = sorted(when for swipe in swipes if (when := parse_when(swipe.get("date", ""), year)))
    spots = Counter(spot_name(transaction.get("location", "")) for transaction in transactions)
    quantities = item_quantities(transactions)
    signature = signature_order(transactions)
    candidates = []

    def add(score, key, mark, title, description):
        candidates.append({
            "score": score, "key": key, "mark": mark,
            "title": title, "description": description,
        })

    def qty_with(*words):
        return sum(qty for name, qty in quantities.items() if any(w in name.casefold() for w in words))

    matcha = qty_with("matcha")
    if matcha >= 3:
        add(90 + matcha, "matcha", "M", "Matcha Machine", f"{matcha} matcha drinks and counting.")

    coffee, _ = _count_matching(transactions, COFFEE_WORDS, skip=("matcha", "hot chocolate"))
    if coffee >= 10:
        add(84 + min(coffee, 20) / 2, "coffee", "JOE", "Caffeine Dependent", f"{coffee} coffee drinks this semester.")

    energy, flavors = _count_matching(transactions, ENERGY_WORDS)
    if energy >= 8:
        flavor_note = f" across {flavors} flavors" if flavors >= 3 else ""
        add(84 + min(energy, 20) / 2, "energy", "NRG", "Energy Drink Era", f"{energy} energy drinks{flavor_note}.")

    if len(swipes) >= 80:
        add(82 + min(len(swipes), 120) / 15, "caf", "C", "Caf Regular", f"{len(swipes)} meal swipes this semester.")

    night = sum(when.hour >= 20 or when.hour < 4 for when, _ in timed)
    if night >= 2:
        add(72 + night, "night", "PM", "Night Owl", f"{night} food runs after 8 PM.")

    mornings = {when.date() for when in [*caf_times, *(w for w, _ in timed)] if when.hour * 60 + when.minute < 10 * 60 + 30}
    if len(mornings) >= 20:
        add(78 + min(len(mornings), 40) / 4, "breakfast", "AM", "Breakfast Club",
            f"{len(mornings)} mornings eating before 10:30 AM.")

    second = sum(
        any(timedelta(0) <= when - swipe <= SECOND_DINNER_WINDOW for swipe in caf_times)
        for when, spot in timed if spot != "Tiger Express"
    )
    if second >= 6:
        add(78 + second, "second-dinner", "2ND", "Second Dinner", f"{second} food runs right after a Caf meal.")

    taco_tuesdays = sum(when.weekday() == 1 and spot == "Tacos 4 Life" for when, spot in timed)
    if taco_tuesdays >= 2:
        add(86 + taco_tuesdays, "taco-tuesday", "T4L", "Taco Tuesday", f"{taco_tuesdays} Tuesday trips to Tacos 4 Life.")

    # Spots with their own regular badge once the visits add up, even when not #1.
    regulars = {"Dr Jacks": (15, "DJ", "Dr. Jack's Regular", "Dr. Jack's"),
                "Tiger Mart": (10, "TM", "Tiger Mart Regular", "Tiger Mart")}
    earned = set()
    for spot, (minimum, mark, title, label) in regulars.items():
        if spots[spot] >= minimum:
            earned.add(spot)
            add(80 + min(spots[spot], 40) / 4, f"regular-{spot}", mark, title, f"{spots[spot]} visits to {label}.")

    if spots:
        favorite, visits = spots.most_common(1)[0]
        share = visits / max(1, len(transactions))
        if favorite not in earned and visits >= 4 and share >= 0.35:
            titles = {
                "CFA": ("CFA", "Chick-fil-A Loyalist"),
                "Tacos 4 Life": ("T4L", "Taco Loyalist"),
                "Dr Jacks": ("DJ", "Dr. Jack's Regular"),
            }
            mark, title = titles.get(favorite, ("#1", f"{favorite} Regular"))
            add(65 + share * 20, "favorite-spot", mark, title,
                f"{visits} of your {len(transactions)} visits were here.")

    if signature and signature["count"] >= 6:
        add(76 + signature["count"], "usual", "x2", "Same Order Energy",
            f"You ordered your usual {signature['count']} times.")

    biggest = max(transactions, key=lambda t: float(t.get("grand_total") or 0), default=None)
    if biggest and float(biggest.get("grand_total") or 0) >= 25:
        add(74 + float(biggest["grand_total"]) / 5, "squad", "$$", "Feeding the Squad",
            f"${float(biggest['grand_total']):.2f} in one order at {spot_name(biggest.get('location', ''))}.")

    fountain = qty_with("fountain drink")
    if fountain >= 15:
        add(76 + min(fountain, 30) / 3, "refill", "SIP", "Refill Royalty", f"{fountain} fountain drinks.")

    per_day = Counter(when.date() for when, _ in timed)
    double_days = sum(1 for visits in per_day.values() if visits >= 2)
    if double_days >= 15:
        add(74 + min(double_days, 30) / 3, "double-back", "DBL", "Double Back",
            f"{double_days} days you came back for more.")

    desserts, _ = _count_matching(transactions, DESSERT_WORDS, skip=("muscle milk",))
    if desserts >= 6:
        add(72 + desserts, "sweet-tooth", "YUM", "Sweet Tooth", f"{desserts} desserts and sweets.")

    standard_spots = {"CFA", "Dr Jacks", "Tacos 4 Life"}
    if standard_spots.issubset(spots):
        add(64, "explorer", "OBU", "Campus Explorer", "You visited every major campus food spot.")

    weekend = sum(when.weekday() >= 5 for when, _ in timed)
    if weekend >= 3 and weekend / max(1, len(timed)) >= 0.25:
        add(62 + weekend, "weekend", "W", "Weekend Warrior", f"{weekend} weekend food runs.")

    if len(quantities) >= 10:
        add(45 + min(len(quantities), 30), "variety", "+", "Menu Mixer",
            f"You tried {len(quantities)} different items.")

    if not candidates:
        add(1, "first", "1", "First Course", f"{len(transactions)} campus food visits logged.")

    chosen = sorted(candidates, key=lambda badge: (-badge["score"], badge["key"]))[:3]
    return [{k: badge[k] for k in ("mark", "title", "description")} for badge in chosen]


# Tiger Express is the Caf's lunch-only grab-and-go counter: its swipes count as Caf
# swipes, and students with at least TIGER_MINIMUM of them get a slide of their own.
TIGER_MINIMUM = 2
TIGER_TIERS = ((10, "Tiger Express VIP", "grab-and-go lunches. They know your order."),
               (5, "Express Lane", "Tiger Express runs. You know the fast way."),
               (2, "Grab & Go", "lunches grabbed at Tiger Express."))


def tiger_express(swipes: list, metadata: dict) -> dict | None:
    """Grab-and-go swipes at Tiger Express: count, share of Caf swipes, usual day and
    hour, a count tier badge and (when one weekday is at least half of them) a day badge."""
    year = date.fromisoformat(metadata["startdate"]).year
    moments = [
        when for swipe in swipes
        if spot_name(swipe.get("location", "")) == "Tiger Express"
        and (when := parse_when(swipe.get("date", ""), year))
    ]
    count = len(moments)
    if count < TIGER_MINIMUM:
        return None

    badges = []
    for minimum, title, blurb in TIGER_TIERS:
        if count >= minimum:
            badges.append({"mark": "TX", "title": title, "description": f"{count} {blurb}"})
            break
    weekdays = Counter(when.weekday() for when in moments)
    day = max(range(7), key=lambda d: (weekdays[d], -d))
    if weekdays[day] >= 2 and weekdays[day] * 2 >= count:
        badges.append({"mark": WEEKDAYS[day][:3].upper(), "title": f"{WEEKDAYS[day]} Ritual",
                       "description": f"{weekdays[day]} of your {count} Tiger Express runs were on {WEEKDAYS[day]}s."})

    hours = Counter(when.hour for when in moments)
    return {
        "count": count,
        "share": round(count / len(swipes) * 100),
        "day": WEEKDAYS[day],
        "hour": _hour_label(max(range(24), key=lambda h: (hours[h], -h))),
        "badges": badges,
    }


def _percentile(values: list[float], value: float) -> dict:
    return {
        "top": max(1, round(sum(other >= value for other in values) / len(values) * 100)),
        "more_than": round(sum(other < value for other in values) / len(values) * 100),
    }


def campus_percentiles(current: dict, students: list[dict], minimum: int = 3) -> list[dict]:
    """Strong campus ranks, shown only once at least three students qualify."""
    if len(students) < minimum:
        return []

    candidates = []

    def metric(field, label, value_text, priority):
        value = current.get(field)
        values = [student.get(field) for student in students if isinstance(student.get(field), (int, float))]
        if not isinstance(value, (int, float)) or value <= 0 or len(values) < minimum:
            return
        rank = _percentile(values, value)
        if rank["top"] <= 50:
            candidates.append({
                **rank, "label": label, "detail": value_text(value, len(values)),
                "priority": priority,
            })

    metric("visits", "campus diners by visits", lambda value, _: f"{value:g} visits", 2)
    metric("caf_swipes", "Caf regulars", lambda value, _: f"{value:g} meal swipes", 3)
    metric("total_spend", "campus Flex spenders", lambda value, _: f"${value:,.2f} spent", 4)
    metric("unique_items", "campus menu explorers", lambda value, _: f"{value:g} different items", 5)

    item_map = current.get("items") or {}
    if item_map:
        top_item, quantity = max(item_map.items(), key=lambda pair: (pair[1], pair[0]))
        peers = [
            student["items"].get(top_item, 0)
            for student in students
            if isinstance(student.get("items"), dict) and student["items"].get(top_item, 0) > 0
        ]
        if len(peers) >= minimum:
            rank = _percentile(peers, quantity)
            if rank["top"] <= 50:
                candidates.append({
                    **rank,
                    "label": f"{top_item} fans",
                    "detail": f"{quantity} orders",
                    "priority": 1,
                })

    candidates.sort(key=lambda insight: (insight["top"], insight["priority"], insight["label"]))
    return [{k: insight[k] for k in ("top", "more_than", "label", "detail")} for insight in candidates[:3]]


MEAL_WINDOW = timedelta(minutes=1)
# (minimum shared meals, title), highest first.
MEAL_TIERS = ((30, "Caf Soulmates"), (10, "Caf Crew"))


def friend_match(left: dict, right: dict) -> dict:
    """Compare aggregate profiles and one-to-one Caf arrivals within MEAL_WINDOW."""
    left_items = left.get("items") or {}
    right_items = right.get("items") or {}
    left_names = set(left_items)
    right_names = set(right_items)
    union = left_names | right_names
    compatibility = round(len(left_names & right_names) / len(union) * 100) if union else 0
    shared = sorted(
        left_names & right_names,
        key=lambda name: (-min(left_items[name], right_items[name]), name.casefold()),
    )[:3]
    if compatibility >= 50:
        vibe = "Same Order Energy"
    elif compatibility >= 25:
        vibe = "Dining Cousins"
    else:
        vibe = "Opposite Menus"

    # Greedy two-pointer over both sorted swipe lists: pair the earliest swipes
    # that fall within the window, so each swipe is used at most once.
    left_times = sorted(left.get("_caf_times") or [])
    right_times = sorted(right.get("_caf_times") or [])
    together = 0
    i = j = 0
    while i < len(left_times) and j < len(right_times):
        a, b = left_times[i], right_times[j]
        if abs(a - b) <= MEAL_WINDOW:
            together += 1
            i += 1
            j += 1
        elif a < b:
            i += 1
        else:
            j += 1
    meal_title = next((title for minimum, title in MEAL_TIERS if together >= minimum), None)

    return {
        "compatibility": compatibility,
        "vibe": vibe,
        "shared": shared,
        "likely_meals": together,
        "meal_title": meal_title,
    }


def _longest_run(days: set) -> dict | None:
    """Longest run of consecutive calendar days; the later run wins a tie."""
    best = None
    for day in sorted(days):
        if day - timedelta(days=1) in days:
            continue
        end = day
        while end + timedelta(days=1) in days:
            end += timedelta(days=1)
        length = (end - day).days + 1
        if best is None or length >= best["days"]:
            best = {"days": length, "start": day, "end": end}
    return best


def _span(run: dict) -> str:
    start, end = run["start"], run["end"]
    tail = str(end.day) if (start.year, start.month) == (end.year, end.month) else f"{end:%b} {end.day}"
    return f"{start:%b} {start.day}–{tail}"


def _event_days(events: list, year: int) -> set:
    return {when.date() for event in events if (when := parse_when(event.get("date", ""), year))}


def dining_streaks(transactions: list, swipes: list, metadata: dict) -> dict | None:
    """Longest runs of consecutive days at the Caf, and eating on campus at all.
    None when the on-campus streak is under 3 days (nothing worth a slide)."""
    year = date.fromisoformat(metadata["startdate"]).year
    caf_days = _event_days(swipes, year)
    food = _longest_run(caf_days | _event_days(transactions, year))
    if not food or food["days"] < 3:
        return None
    caf = _longest_run(caf_days)
    return {
        "caf": {"days": caf["days"], "span": _span(caf)} if caf and caf["days"] >= 2 else None,
        "food": {"days": food["days"], "span": _span(food)},
    }


def campus_activity(transactions: list, swipes: list, year: int) -> dict:
    """Per-student weekday/hour counts and best Caf streak, stored in the campus stats."""
    weekdays, hours = [0] * 7, [0] * 24
    for event in (*transactions, *swipes):
        when = parse_when(event.get("date", ""), year)
        if when:
            weekdays[when.weekday()] += 1
            hours[when.hour] += 1
    caf = _longest_run(_event_days(swipes, year))
    return {"weekdays": weekdays, "hours": hours, "caf_streak": caf["days"] if caf else 0}


def _hour_label(hour: int) -> str:
    return f"{hour % 12 or 12} {'AM' if hour < 12 else 'PM'}"


def campus_wrapped(students: list[dict], minimum: int = 3) -> dict | None:
    """Campus-wide totals from each student's latest run. None until `minimum` students
    have wrapped; an item, spot or busiest-time stat only shows once `minimum` students
    contribute to it, so no single student's habits can be read off the page."""
    if len(students) < minimum:
        return None
    item_orders, item_students = Counter(), Counter()
    spot_visits, spot_students = Counter(), Counter()
    weekdays, hours, timed = [0] * 7, [0] * 24, 0
    for student in students:
        for name, qty in (student.get("items") or {}).items():
            item_orders[name] += qty
            item_students[name] += 1
        for spot, visits in (student.get("spots") or {}).items():
            spot_visits[spot] += visits
            spot_students[spot] += 1
        if student.get("weekdays") and student.get("hours"):
            timed += 1
            weekdays = [a + b for a, b in zip(weekdays, student["weekdays"])]
            hours = [a + b for a, b in zip(hours, student["hours"])]

    def ranked(totals: Counter, people: Counter, limit: int) -> list[dict]:
        names = sorted((n for n in totals if people[n] >= minimum), key=lambda n: (-totals[n], n.casefold()))
        return [{"name": n, "count": totals[n]} for n in names[:limit]]

    busiest = None
    if timed >= minimum and sum(weekdays):
        day = max(range(7), key=lambda i: (weekdays[i], -i))
        hour = max(range(24), key=lambda h: (hours[h], -h))
        busiest = {"day": WEEKDAYS[day], "hour": _hour_label(hour),
                   "weekdays": [{"day": WEEKDAYS[i], "count": weekdays[i]} for i in range(7)]}
    spend = round(sum(s.get("total_spend") or 0 for s in students), 2)
    record = max((s.get("caf_streak") or 0 for s in students), default=0)
    return {
        "spend": spend,
        "visits": sum(s.get("visits") or 0 for s in students),
        "caf_swipes": sum(s.get("caf_swipes") or 0 for s in students),
        "nuggets": sum(s.get("nuggets") or 0 for s in students),
        "top_items": ranked(item_orders, item_students, 5),
        "top_spots": ranked(spot_visits, spot_students, 4),
        "busiest": busiest,
        "record_streak": record if record >= 2 else None,
    }
