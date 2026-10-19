# Maps Atrium's abbreviated item names to display-friendly full names.
# Keys are lowercase for case-insensitive matching.
# Add new entries here as you encounter new abbreviations.

# Maps Atrium's abbreviated item names to display-friendly full names.
# Keys are lowercase for case-insensitive matching.

import re

NAME_MAP: dict[str, str] = {
    # Fountain drinks
    "32 oz fou": "32 oz Fountain Drink",
    "32 oz fountain": "32 oz Fountain Drink",
    "22 oz fountain drink": "22 oz Fountain Drink",
    "22 oz foun": "22 oz Fountain Drink",
    "fontn hot chocolt": "Hot Chocolate",
    "cfa soft drink 32oz": "CFA Soft Drink 32 oz",
    "soda": "Fountain Drink",

    # CFA sandwiches / combos
    "cfa sand": "CFA Sandwich",
    "cfa sandwich combo": "CFA Sandwich Combo",
    "cfa spicy sndwich": "CFA Spicy Sandwich",
    "cfa biscuit": "CFA Biscuit",
    "cfa biscuit w/ chix": "CFA Biscuit with Chicken",
    "cfa chick-n-mini 4pc": "CFA Chick-n-Minis 4pc",
    "upsize combo": "Upsize Combo",
    "upsize co": "Upsize Combo",

    # CFA nuggets / fries / sides
    "cfa nuggets-8 count": "CFA Nuggets 8pc",
    "cfa nuggets-12 count": "CFA Nuggets 12pc",
    "cfa nugge": "CFA Nuggets",
    "8pc nugget combo": "8pc Nugget Combo",
    "12pc nugget combo": "12pc Nugget Combo",
    "cfa wffle frie mdium": "CFA Waffle Fries Medium",
    "cfa wffle fries lrg": "CFA Waffle Fries Large",
    "cfa wffle f": "CFA Waffle Fries",
    "cfa fruit cup": "CFA Fruit Cup",
    "frsh fruit cup to go": "Fresh Fruit Cup",
    "cfa yogurt parfait": "CFA Yogurt Parfait",
    "cfa hash browns": "CFA Hash Browns",

    # Drinks / boba
    "24oz paradise punch": "24 oz Paradise Punch",
    "arnold palmer lg": "Arnold Palmer Large",
    "drag fruit berry": "Dragon Fruit Berry",
    "mango pop boba": "Mango Boba",
    "boba toppings": "Boba Toppings",
    "fruit chille": "Fruit Chiller Large",
    "fruit chiller large": "Fruit Chiller Large",
    "lemonade": "Lemonade",
    "whit hot 20oz": "White Hot Chocolate 20 oz",
    "ocspry cran-grape": "Ocean Spray Cran-Grape",
    "propel water 20oz": "Propel Water 20 oz",
    "purified water": "Purified Water",

    # Coffee / specialty drinks
    "caramel latte": "Caramel Latte",

    # Tacos 4 Life
    "cbr quesadilla": "Chicken Bacon Ranch Quesadilla",
    "grilld chickn qusdil": "Grilled Chicken Quesadilla",
    "quesadilla": "Quesadilla",
    "guac and chips sm": "Guac and Chips (Small)",

    # Dr. Jack's / breakfast
    "hb scramble bowl": "Hashbrown Scramble Bowl",
    "hbs burrito": "Hashbrown Scramble Burrito",
    "scb biscuit": "Sausage Chicken Biscuit",

    # Snacks / sides
    "mac and cheese": "Mac and Cheese",
    "waffle chips": "Waffle Chips",
    "cookie": "Cookie",
    "fig bar/strawberry": "Fig Bar (Strawberry)",
    "uncrustable": "Uncrustable",
    "hony rostd bbq suc": "Honey Roasted BBQ Sauce",

    # Other
    "refuel": "ReFuel",
}


def expand_name(raw: str) -> str:
    """Return the full display name for a raw Atrium item name, or the original if unknown."""
    return NAME_MAP.get(raw.lower().strip(), raw)


# One name per menu item. The cleanup model names each receipt on its own, so the same
# item can come back as "Waffle Fry" on one receipt and "Waffle Fries" on another, which
# splits its count. Keys are lowercase; SPOT_CANONICAL applies only at that spot.
CANONICAL: dict[str, str] = {
    "spicy sandwich": "Spicy Chicken Sandwich",
    "waffle fry": "Waffle Fries",
    "chick-fil-a waffle fries": "Waffle Fries",
    "chick-fil-a biscuit": "Chicken Biscuit",
    "chargrill": "Grilled Chicken Sandwich",
    "grilled club": "Grilled Chicken Club Sandwich",
    "hashbrowns": "Hash Browns",
    "hb scramble burrito": "Hash Brown Scramble Burrito",
    "hashbrown scramble burrito": "Hash Brown Scramble Burrito",
    "hb scramble bowl": "Hash Brown Scramble Bowl",
    "hashbrown scramble bowl": "Hash Brown Scramble Bowl",
    "cbr quesadilla": "Chicken Bacon Ranch Quesadilla",
    "cbr taco": "Chicken Bacon Ranch Taco",
    # Tacos 4 Life board names (menus.md)
    "queso and chips": "Queso & Chips",
    "chips and queso": "Queso & Chips",
    "chips & queso": "Queso & Chips",
    "buffalo chicken taco": "Buffalo Fried Chicken Taco",
    "refried beans": "Refried Black Beans",
    "chicken quesadilla": "Grilled Chicken Quesadilla",
    "queso rice bowl": "Grilled Chicken & Queso Rice Bowl",
    "sopapilla": "Sopapillas",
    # Dr. Jack's
    "matcha tea": "Matcha Latte",
    "fontn hot chocolt": "Hot Chocolate",
}
SPOT_CANONICAL: dict[tuple[str, str], str] = {
    ("CFA", "cookie"): "Chocolate Chunk Cookie",
}
# Names whose size or flavor belongs in the detail: name -> (item, detail to add).
DETAIL_ALIASES: dict[str, tuple[str, str]] = {
    "full queso rice bowl": ("Grilled Chicken & Queso Rice Bowl", "Full"),
    "half queso rice bowl": ("Grilled Chicken & Queso Rice Bowl", "Half"),
    "alani": ("Alani Nu", ""),
    "alani - hawaiian": ("Alani Nu", "Hawaiian"),
    "alani nu - hawaiian": ("Alani Nu", "Hawaiian"),
}
# Nuggets keep their count in the name. CFA's 4-count nuggets are always Chick-n-Minis.
NUGGETS: dict[str, str] = {
    "chick-fil-a nuggets": "Chick-fil-A Nuggets", "nuggets": "Chick-fil-A Nuggets",
    "chick-fil-a grilled nuggets": "Chick-fil-A Grilled Nuggets", "grilled nuggets": "Chick-fil-A Grilled Nuggets",
    "chick-fil-a minis": "Chick-n-Minis", "chick-n-minis": "Chick-n-Minis", "minis": "Chick-n-Minis",
}
_MEAL = re.compile(r"^(.*?)\s+(?:meal|combo)$", re.IGNORECASE)
_COUNTED = re.compile(r"^(.*?)\s*\((\d+)\s*ct\)$", re.IGNORECASE)
_COUNT_PART = re.compile(r"^(\d+)\s*(?:ct|count|pc)$", re.IGNORECASE)


def _join(*parts: str) -> str:
    seen, out = set(), []
    for part in (p.strip() for chunk in parts for p in chunk.split(",")):
        if part and part.casefold() not in seen:
            seen.add(part.casefold())
            out.append(part)
    return ", ".join(out)


def canonical_item(name: str, detail: str = "", spot: str | None = None) -> tuple[str, str]:
    """(name, detail) with one name per menu item: a meal or combo counts as its main
    item (detail "Meal"), nugget counts live in the name, and known variants share a name."""
    name, detail = name.strip(), _join(detail)
    meal = _MEAL.match(name)
    if meal:
        name, detail = meal.group(1).strip(), _join("Meal", detail)
    if name.lower() in DETAIL_ALIASES:
        name, extra = DETAIL_ALIASES[name.lower()]
        detail = _join(extra, detail)

    counted = _COUNTED.match(name)
    base, count = (counted.group(1).strip(), counted.group(2)) if counted else (name, None)
    if base.lower() in NUGGETS:
        parts = [p.strip() for p in detail.split(",") if p.strip()]
        for part in parts:
            if not count and (m := _COUNT_PART.match(part)):
                count = m.group(1)
        detail = ", ".join(p for p in parts if not _COUNT_PART.match(p))
        kind = NUGGETS[base.lower()]
        if kind == "Chick-fil-A Nuggets" and count == "4":
            kind = "Chick-n-Minis"
        return (f"{kind} ({count} ct)" if count else kind), detail

    key = name.lower()
    return SPOT_CANONICAL.get((spot, key)) or CANONICAL.get(key) or name, detail
