import json
import os
import tempfile
import unittest

import analysis
from analysis import _first_json_object


class ModelReplyTests(unittest.TestCase):
    def test_takes_the_first_object_and_ignores_what_follows(self):
        reply = '```json\n{"roast": "x", "money": ["a"]}\n```\nNote: {not json} {"b": 2}'
        self.assertEqual(_first_json_object(reply), {"roast": "x", "money": ["a"]})

    def test_reply_without_an_object_is_an_error(self):
        with self.assertRaises(ValueError):
            _first_json_object("Sorry, I can't help with that.")


class NormalizeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = (analysis.CACHE_FILE, analysis._chat)
        analysis.CACHE_FILE = os.path.join(self.tmp.name, "cache.json")

    def tearDown(self):
        analysis.CACHE_FILE, analysis._chat = self.original
        self.tmp.cleanup()

    def test_raw_register_names_never_reach_the_slides(self):
        receipt = lambda: {"location": "Tacos 1", "items": [
            {"name": "CBR Quesadilla", "qty": 1, "subtotal": 11.70}, {"name": "SODA", "qty": 1, "subtotal": 3.00}]}
        # The model leaves register text as-is...
        analysis._chat = lambda *_, **__: {"receipts": [{"id": 0, "items": [
            {"item": "CBR QUESADILLA", "detail": "", "qty": 1, "total": 11.70},
            {"item": "SODA", "detail": "", "qty": 1, "total": 3.00}]}]}
        fresh = [receipt()]
        analysis.normalize(fresh)
        self.assertEqual([i["item"] for i in fresh[0]["clean_items"]], ["Chicken Bacon Ranch Quesadilla", "Fountain Drink"])
        # ...and receipts cleaned before this fix come back from the cache fixed too.
        with open(analysis.CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({analysis._cache_key(analysis._signature(receipt())): [
                {"item": "SODA", "detail": "", "qty": 1, "total": 14.70}]}, f)
        analysis._chat = lambda *_, **__: self.fail("cache hit should not call the model")
        cached = [receipt()]
        analysis.normalize(cached)
        self.assertEqual(cached[0]["clean_items"][0]["item"], "Fountain Drink")

    def test_meals_count_as_their_main_item_and_variants_share_one_name(self):
        receipts = [
            {"location": "CFA 1", "clean_items": [{"item": "Chick-fil-A Sandwich Meal", "detail": "Sub Mac & Cheese", "qty": 1, "total": 10.95}]},
            {"location": "CFA 2", "clean_items": [{"item": "Spicy Sandwich", "detail": "", "qty": 1, "total": 5.65},
                                                  {"item": "Waffle Fry", "detail": "Medium", "qty": 1, "total": 2.59},
                                                  {"item": "Cookie", "detail": "", "qty": 1, "total": 1.79}]},
            {"location": "Dr Jacks", "clean_items": [{"item": "Cookie", "detail": "", "qty": 1, "total": 2.00}]},
        ]
        analysis.canonicalize(receipts)
        self.assertEqual(receipts[0]["clean_items"][0], {"item": "Chick-fil-A Sandwich", "detail": "Meal, Sub Mac & Cheese",
                                                          "qty": 1, "total": 10.95})
        self.assertEqual([i["item"] for i in receipts[1]["clean_items"]],
                         ["Spicy Chicken Sandwich", "Waffle Fries", "Chocolate Chunk Cookie"])
        self.assertEqual(receipts[2]["clean_items"][0]["item"], "Cookie")  # CFA's cookie only
        analysis.canonicalize(receipts)  # applying it again changes nothing
        self.assertEqual(receipts[0]["clean_items"][0]["detail"], "Meal, Sub Mac & Cheese")

    def test_nugget_counts_minis_and_combos(self):
        from name_map import canonical_item
        cases = {
            ("Chick-fil-A Nuggets (4 ct) Meal", ""): ("Chick-n-Minis (4 ct)", "Meal"),
            ("Chick-fil-A Minis (4 ct)", ""): ("Chick-n-Minis (4 ct)", ""),
            ("Chick-fil-A Nuggets", "8 ct"): ("Chick-fil-A Nuggets (8 ct)", ""),
            ("Grilled Nuggets", "12 ct, Sub Mac n Cheese"): ("Chick-fil-A Grilled Nuggets (12 ct)", "Sub Mac n Cheese"),
            ("Chargrill Combo", ""): ("Grilled Chicken Sandwich", "Meal"),
            ("Full Queso Rice Bowl", "Add Chicken"): ("Grilled Chicken & Queso Rice Bowl", "Full, Add Chicken"),
            ("Chips & Queso", "Small"): ("Queso & Chips", "Small"),
        }
        for (name, detail), expected in cases.items():
            self.assertEqual(canonical_item(name, detail, "CFA"), expected, name)

    def test_a_receipt_that_fails_in_its_batch_is_retried_alone(self):
        big = {"location": "Tacos 1", "items": [{"name": "Classic Beef Taco", "qty": 1, "subtotal": 4.95},
                                                {"name": "Hungry for More", "qty": 1, "subtotal": 5.00}]}
        calls = []

        def chat(_system, receipts, **_):
            calls.append(receipts)
            total = 9.95 if len(calls) > 1 else 4.95  # first answer drops the meal price
            return {"receipts": [{"id": 0, "items": [{"item": "Classic Beef Taco", "detail": "Meal", "qty": 1, "total": total}]}]}
        analysis._chat = chat
        analysis.normalize([big])
        self.assertEqual(len(calls), 2)
        self.assertEqual(big["clean_items"][0]["total"], 9.95)


if __name__ == "__main__":
    unittest.main()
