# Maps Atrium's abbreviated item names to display-friendly full names.
# Keys are lowercase for case-insensitive matching.
# Add new entries here as you encounter new abbreviations.

# Maps Atrium's abbreviated item names to display-friendly full names.
# Keys are lowercase for case-insensitive matching.

NAME_MAP: dict[str, str] = {
    # Fountain drinks
    "32 oz fou": "32 oz Fountain Drink",
    "32 oz fountain": "32 oz Fountain Drink",
    "22 oz fountain drink": "22 oz Fountain Drink",
    "22 oz foun": "22 oz Fountain Drink",
    "fontn hot chocolt": "Hot Chocolate",
    "cfa soft drink 32oz": "CFA Soft Drink 32 oz",

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
