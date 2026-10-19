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


if __name__ == "__main__":
    unittest.main()
