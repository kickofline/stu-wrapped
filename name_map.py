# Maps Atrium's abbreviated item names to display-friendly full names.
# Keys are lowercase for case-insensitive matching.
# Add new entries here as you encounter new abbreviations.

NAME_MAP: dict[str, str] = {
    # Fountain drinks
    "32 oz fou": "32 oz Fountain Drink",
    "22 oz fountain drink": "22 oz Fountain Drink",
    "22 oz foun": "22 oz Fountain Drink",
    "fontn hot chocolt": "Fountain Hot Chocolate",

    # CFA sandwiches / combos
    "cfa sand": "CFA Sandwich",
    "cfa sandwich combo": "CFA Sandwich Combo",
    "cfa biscuit": "CFA Biscuit",
    "cfa biscuit w/ chix": "CFA Biscuit with Chicken",
    "cfa chick-n-mini 4pc": "CFA Chick-n-Minis 4pc",
    "upsize combo": "Upsize Combo",
    "upsize co": "Upsize Combo",

    # CFA nuggets / fries / sides
    "cfa nuggets-8 count": "CFA Nuggets 8pc",
    "cfa nugge": "CFA Nuggets",
    "cfa nuggets-12 count": "CFA Nuggets 12pc",
    "8pc nugget combo": "8pc Nugget Combo",
    "cfa wffle frie mdium": "CFA Waffle Fries Medium",
    "cfa wffle f": "CFA Waffle Fries",
    "cfa fruit cup": "CFA Fruit Cup",
    "cfa yogurt parfait": "CFA Yogurt Parfait",

    # Drinks / boba
    "24oz paradise punch": "24 oz Paradise Punch",
    "arnold palmer lg": "Arnold Palmer Large",
    "drag fruit berry": "Dragon Fruit Berry",
    "mango pop boba": "Mango Pop Boba",
    "fruit chille": "Fruit Chiller Large",
    "fruit chiller large": "Fruit Chiller Large",

    # Tacos 4 Life
    "cbr quesadilla": "CBR Quesadilla",
    "grilld chickn qusdil": "Grilled Chicken Quesadilla",
    "guac and chips sm": "Guac and Chips (Small)",

    # Dr. Jack's
    "hb scramble bowl": "HB Scramble Bowl",
    "refuel": "ReFuel",
}


def expand_name(raw: str) -> str:
    """Return the full display name for a raw Atrium item name, or the original if unknown."""
    return NAME_MAP.get(raw.lower().strip(), raw)
