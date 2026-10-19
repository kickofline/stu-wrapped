"""Create (or remove) 10 fabricated students for testing every Wrapped scenario.

Runs the real post-scrape pipeline on made-up purchases and swipes: DeepSeek item
cleanup and slides (analysis.normalize/analyze), the saved result, and the campus
stats, so the fakes show up on the Campus Leaderboard and in everyone's rankings.

    python tools/fake_students.py           # (re)create; prints share links
    python tools/fake_students.py --remove  # delete every fake result and stats entry

Fakes are listed in data/test_students.json and use "test-" student keys.
"""
import json
import os
import random
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import analysis  # noqa: E402
import app  # noqa: E402

MANIFEST = os.path.join(app.DATA_DIR, "test_students.json")
SITE = "https://stu.exposed"
YEAR = 2026
FIRST_DAY, LAST_DAY = date(YEAR, 8, 19), date(YEAR, 10, 18)

# (spot, register line, price); add-ons ride on the line after their item.
CFA = {
    "sandwich": ("CFA 1", "CFA SANDWICH", 5.25), "spicy": ("CFA 2", "SPICY SANDWICH", 5.65),
    "nuggets": ("CFA 2", "8 COUNT NUGGET", 6.15), "combo": ("CFA 2", "8 COUNT COMBO", 9.45),
    "grilled": ("CFA 1", "ChrGrilld Nuggts 12", 8.69), "biscuit": ("CFA 1", "CHICKEN BISCUIT", 3.85),
    "parfait": ("CFA 1", "YOGURT PARFAIT", 4.95), "shake": ("CFA 2", "OREO SHAKE", 4.89),
    "lemonade": ("CFA 1", "Frosted Lemonade Lg", 4.69), "tea": ("CFA 2", "(L) SWEET TEA", 2.85),
    "fountain": ("CFA 1", "24 OZ FOUNTAIN", 2.39), "fries": ("CFA 1", "WAFFLE FRY MED", 2.65),
}
JACKS = {
    "matcha": ("Dr Jacks", "(L)- Matcha Tea", 6.49), "matcha16": ("Dr Jacks", "16 Oz Matcha Latte", 5.49),
    "latte": ("Dr Jacks", "(L) - Latte", 6.39), "latte16": ("Dr Jacks", "Latte 16oz", 5.89),
    "macchiato": ("Dr Jacks", "(L)Caramel Macchiato", 8.09), "whitehot": ("Dr Jacks", "(L) White Hot Choc", 6.19),
    "coldbrew": ("Dr Jacks", "ICED COLD BREW", 4.75), "muffin": ("Dr Jacks", "BLUEBERRY MUFFIN", 3.25),
    "almond": ("Dr Jacks", "Almond Milk", 0.75), "vanilla": ("Dr Jacks", "Vanilla Syrup", 0.60),
}
TACOS = {
    "beef": ("Tacos 1", "Classic Beef Taco", 4.95), "chicken": ("Tacos 1", "Classic Chicken Taco", 4.95),
    "fried": ("Tacos 1", "Fried Chicken Taco", 5.10), "fajita": ("Tacos 1", "Fajita Chicken Taco", 5.10),
    "buffalo": ("Tacos 1", "Buffalo Chicken Taco", 5.60), "bowl": ("Tacos 1", "Full Queso Rice Bowl", 9.00),
    "addchicken": ("Tacos 1", "Add Chicken", 0.00), "punch": ("Tacos 1", "PARADISE PUNCH", 3.60),
    "beans": ("Tacos 1", "Refried Beans", 2.95), "queso": ("Tacos 1", "CHIPS & QUESO", 3.95),
}
MENU = {**CFA, **JACKS, **TACOS}


def stamp(when: datetime) -> str:
    return f"{when:%b} {when.day}, {when.year} {when:%I:%M%p}"


def days(rng, n, weekdays=range(7), start=FIRST_DAY, end=LAST_DAY):
    pool = [start + timedelta(d) for d in range((end - start).days + 1)]
    pool = [d for d in pool if d.weekday() in weekdays]
    return sorted(rng.sample(pool, min(n, len(pool))))


def at(day: date, hour: int, minute: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute)


class Student:
    def __init__(self, slug, name, scenario, flex, meals, seed):
        self.slug, self.name, self.scenario = slug, name, scenario
        self.flex_left, self.meals_left = flex, meals
        self.rng = random.Random(seed)
        self.orders, self.swipes = [], []

    def buy(self, when: datetime, *keys):
        self.orders.append((when, list(keys)))

    def swipe(self, when: datetime):
        self.swipes.append(when)

    def result_inputs(self):
        transactions = []
        for n, (when, keys) in enumerate(self.orders):
            items = [{"name": MENU[k][1], "modifier": "", "unit_price": MENU[k][2], "qty": 1,
                      "promo": "", "subtotal": MENU[k][2]} for k in keys]
            total = round(sum(i["subtotal"] for i in items), 2)
            transactions.append({"date": when, "location": MENU[keys[0]][0],
                                 "transaction_id": f"9{self.slug[:3].upper()}{n:04d}",
                                 "amount": -total, "grand_total": total, "items": items})
        transactions.sort(key=lambda t: t["date"], reverse=True)  # newest first, like Atrium
        balance = self.flex_left
        for tx in transactions:
            tx["balance_after"] = round(balance, 2)
            balance += tx["grand_total"]
            tx["date"] = stamp(tx["date"])
        swipes = [{"date": stamp(w), "location": f"Commons PAX {self.rng.randint(1, 4)}"}
                  for w in sorted(self.swipes, reverse=True)]
        balances = {"Ouachita Dollars": 0.0, "Resident Meals": float(self.meals_left)}
        if self.flex_left is not None:
            balances["Resident Flex"] = self.flex_left
        return transactions, swipes, balances


def build() -> list[Student]:
    out = []

    # 1. Power user: every slide, matcha mornings, a repeated exact CFA order, a long Caf streak.
    maya = Student("maya", "Maya Thompson", "Everything on: badges, usual order, streak, AI slides", 212.40, 38, 1)
    for d in days(maya.rng, 16, range(5)):
        maya.buy(at(d, 8, maya.rng.randint(5, 50)), "matcha", "almond")
    for d in days(maya.rng, 11, range(5)):
        maya.buy(at(d, 12, maya.rng.randint(10, 40)), "sandwich", "fries", "fountain")
    for n, d in enumerate(days(maya.rng, 6)):
        maya.buy(at(d, 15, maya.rng.randint(0, 59)), ["latte16", "parfait", "nuggets"][n % 3])
    for offset in range(24):  # Sep 2 - Sep 25 every day, plus scattered dinners
        maya.swipe(at(date(YEAR, 9, 2) + timedelta(offset), 11, 30 + maya.rng.randint(0, 20)))
    for d in days(maya.rng, 30, start=date(YEAR, 9, 28)):
        maya.swipe(at(d, 17, 30 + maya.rng.randint(0, 25)))
    out.append(maya)

    # 2. Caf Soulmates with Maya (35 swipes within a minute) and Same Order Energy.
    jordan = Student("jordan", "Jordan Ellis", "Caf Soulmates + Same Order Energy with Maya", 149.85, 41, 2)
    for when in jordan.rng.sample(maya.swipes, 35):
        jordan.swipe(when + timedelta(minutes=jordan.rng.randint(0, 1)))
    for d in days(jordan.rng, 8):
        jordan.swipe(at(d, 7, 45 + jordan.rng.randint(0, 10)))
    for d in days(jordan.rng, 14, range(5)):
        jordan.buy(at(d, 12, jordan.rng.randint(5, 45)), "sandwich", "fries", "fountain")
    for d in days(jordan.rng, 7):
        jordan.buy(at(d, 9, jordan.rng.randint(0, 50)), "matcha", "almond")
    out.append(jordan)

    # 3. Caf Crew with Maya (12 shared meals) and Dining Cousins.
    priya = Student("priya", "Priya Nair", "Caf Crew + Dining Cousins with Maya", 268.10, 55, 3)
    for when in priya.rng.sample(maya.swipes, 12):
        priya.swipe(when + timedelta(minutes=priya.rng.choice([-1, 1])))
    for d in days(priya.rng, 20):
        priya.swipe(at(d, 18, 40 + priya.rng.randint(0, 15)))
    for d in days(priya.rng, 12):
        priya.buy(at(d, 10, priya.rng.randint(15, 55)), priya.rng.choice(["latte", "macchiato", "coldbrew"]))
    for d in days(priya.rng, 5):
        priya.buy(at(d, 13, priya.rng.randint(0, 40)), "parfait")
    for d in days(priya.rng, 3):
        priya.buy(at(d, 14, priya.rng.randint(0, 40)), "matcha16")
    for d in days(priya.rng, 2):
        priya.buy(at(d, 12, priya.rng.randint(0, 40)), "nuggets")
    out.append(priya)

    # 4. Flex only, never swiped: Taco Tuesday + Night Owl; Opposite Menus with Maya.
    ethan = Student("ethan", "Ethan Brooks", "Flex only, no Caf swipes; Night Owl + Taco Tuesday; Opposite Menus with Maya", 94.30, 0, 4)
    for d in days(ethan.rng, 9, [1]):
        ethan.buy(at(d, 12, ethan.rng.randint(0, 50)), "beef", "beef", "punch")
    for d in days(ethan.rng, 9, range(5)):
        ethan.buy(at(d, 13, ethan.rng.randint(0, 50)), ethan.rng.choice(["bowl", "fajita", "buffalo"]), "queso")
    for d in days(ethan.rng, 12):
        ethan.buy(at(d, ethan.rng.choice([20, 21, 22]), ethan.rng.randint(0, 59)), ethan.rng.choice(["shake", "lemonade"]))
    out.append(ethan)

    # 5. Swipes only, never touched Flex: no spend slides, long Caf streak, Caf Regular.
    grace = Student("grace", "Grace Kim", "Meal swipes only, no Flex purchases; 26-day Caf streak", 400.00, 12, 5)
    for offset in range(26):
        day = date(YEAR, 8, 24) + timedelta(offset)
        grace.swipe(at(day, 12, grace.rng.randint(0, 40)))
        if grace.rng.random() < 0.6:
            grace.swipe(at(day, 17, grace.rng.randint(15, 50)))
    for d in days(grace.rng, 20, start=date(YEAR, 9, 22)):
        grace.swipe(at(d, 12, grace.rng.randint(0, 40)))
    out.append(grace)

    # 6. Very long name (intro and summary headline fitting).
    max_ = Student("maximilian", "Maximilian Featherstonehaugh-Worthington", "Very long name", 176.55, 60, 6)
    for d in days(max_.rng, 14):
        max_.buy(at(d, max_.rng.choice([11, 12, 13]), max_.rng.randint(0, 59)), "combo", "lemonade")
    for d in days(max_.rng, 8):
        max_.buy(at(d, 16, max_.rng.randint(0, 59)), "whitehot")
    for d in days(max_.rng, 25):
        max_.swipe(at(d, 12, max_.rng.randint(0, 40)))
    out.append(max_)

    # 7. Weekend Warrior + Menu Mixer (tries everything).
    sam = Student("sam", "Sam Ortiz", "Weekend Warrior + Menu Mixer (15+ different items)", 188.20, 70, 7)
    keys = list(MENU)
    keys = [k for k in keys if k not in ("almond", "vanilla", "addchicken")]
    for d in days(sam.rng, 16, [5, 6]):
        sam.buy(at(d, sam.rng.randint(11, 18), sam.rng.randint(0, 59)), sam.rng.choice(keys))
    for d in days(sam.rng, 10, range(5)):
        sam.buy(at(d, sam.rng.randint(11, 18), sam.rng.randint(0, 59)), sam.rng.choice(keys))
    for d in days(sam.rng, 15):
        sam.swipe(at(d, 18, sam.rng.randint(0, 40)))
    out.append(sam)

    # 8. Barely used: one purchase, two swipes (no badges, no streak, no usual order).
    lily = Student("lily", "Lily Chen", "Barely used: 1 purchase, 2 swipes", 344.61, 98, 8)
    lily.buy(at(date(YEAR, 9, 14), 14, 22), "latte16")
    lily.swipe(at(date(YEAR, 8, 27), 12, 15))
    lily.swipe(at(date(YEAR, 10, 2), 17, 48))
    out.append(lily)

    # 9. Single-word name; Early Bird; Chick-fil-A Loyalist; lots of Flex left over.
    noah = Student("noah", "Noah", "One-word name; Early Bird + CFA Loyalist; big Flex surplus", 512.75, 80, 9)
    for d in days(noah.rng, 18, range(5)):
        noah.buy(at(d, 7, noah.rng.randint(30, 58)), "biscuit", "tea")
    for d in days(noah.rng, 4):
        noah.buy(at(d, 12, noah.rng.randint(0, 59)), "spicy")
    for d in days(noah.rng, 10):
        noah.swipe(at(d, 17, noah.rng.randint(0, 50)))
    out.append(noah)

    # 10. Big spender about to run out of Flex well before finals.
    ava = Student("ava", "Ava Martinez", "Big spender; Flex runs out before finals", 36.20, 22, 10)
    for d in days(ava.rng, 40):
        ava.buy(at(d, ava.rng.choice([9, 12, 15, 19, 21]), ava.rng.randint(0, 59)),
                ava.rng.choice(["combo", "grilled", "macchiato", "bowl"]),
                ava.rng.choice(["shake", "lemonade", "punch", "muffin"]))
    for d in days(ava.rng, 18):
        ava.buy(at(d, 10, ava.rng.randint(0, 59)), "macchiato", "vanilla")
    for d in days(ava.rng, 30):
        ava.swipe(at(d, 12, ava.rng.randint(0, 30)))
    out.append(ava)
    return out


def remove() -> None:
    try:
        with open(MANIFEST, encoding="utf-8") as f:
            manifest = json.load(f)
    except FileNotFoundError:
        manifest = {}
    for share_id in manifest:
        for path in (os.path.join(app.RESULTS_DIR, f"{share_id}.json"), os.path.join(app.OG_DIR, f"{share_id}.png")):
            if os.path.exists(path):
                os.remove(path)
    with app._stats_lock:
        stats = app._read_stats()
        stats["students"] = {k: v for k, v in stats["students"].items() if not k.startswith("test-")}
        app._save_stats(stats)
    if os.path.exists(MANIFEST):
        os.remove(MANIFEST)
    print(f"removed {len(manifest)} fake students")


def create() -> None:
    if not analysis.enabled():
        sys.exit("LITELLM_BASE_URL / LITELLM_API_KEY not set: DeepSeek would be skipped")
    remove()
    startdate, enddate = "2026-07-01", "2026-10-19"
    label = "Fall 2026"
    manifest = {}
    for student in build():
        transactions, swipes, balances = student.result_inputs()
        key = f"test-{student.slug}"
        metadata = {"generated_at": datetime.now().isoformat(timespec="seconds"),
                    "startdate": startdate, "enddate": enddate, "name": student.name,
                    "balances": balances, "student_key": key, "run_id": f"test-{student.slug}"}
        ai = None
        try:
            analysis.normalize(transactions)
            ai = analysis.analyze(app._summary(transactions, swipes, metadata, label))
        except Exception as exc:  # same fallback as a real run
            print(f"  {student.name}: DeepSeek failed ({exc}); saved without AI slides")
        share_id = app._save_result(transactions, swipes, metadata, ai)
        app._record_run(transactions, swipes, key, YEAR)
        cleaned = sum("clean_items" in t for t in transactions)
        manifest[share_id] = {"name": student.name, "scenario": student.scenario, "student_key": key}
        print(f"{student.name} | {student.scenario} | {len(transactions)} purchases ({cleaned} cleaned), "
              f"{len(swipes)} swipes | AI: {(ai or {}).get('personality', {}).get('title', 'none')} | "
              f"{SITE}/wrapped/{share_id}", flush=True)
        with open(MANIFEST, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)


if __name__ == "__main__":
    remove() if "--remove" in sys.argv else create()
