import unittest

from analysis import _first_json_object


class ModelReplyTests(unittest.TestCase):
    def test_takes_the_first_object_and_ignores_what_follows(self):
        reply = '```json\n{"roast": "x", "money": ["a"]}\n```\nNote: {not json} {"b": 2}'
        self.assertEqual(_first_json_object(reply), {"roast": "x", "money": ["a"]})

    def test_reply_without_an_object_is_an_error(self):
        with self.assertRaises(ValueError):
            _first_json_object("Sorry, I can't help with that.")


if __name__ == "__main__":
    unittest.main()
