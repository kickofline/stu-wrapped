import unittest
from datetime import datetime

from social import campus_percentiles, dining_badges, friend_match, signature_order


class SocialInsightsTests(unittest.TestCase):
    def test_campus_rank_requires_three_students(self):
        students = [
            {"visits": 30, "caf_swipes": 60, "total_spend": 300, "unique_items": 20,
             "items": {"Matcha Latte": 8}},
            {"visits": 20, "caf_swipes": 40, "total_spend": 200, "unique_items": 15,
             "items": {"Matcha Latte": 4}},
            {"visits": 10, "caf_swipes": 20, "total_spend": 100, "unique_items": 10,
             "items": {"Matcha Latte": 2}},
        ]
        self.assertEqual(campus_percentiles(students[0], students[:2]), [])
        ranks = campus_percentiles(students[0], students)
        self.assertTrue(ranks)
        self.assertTrue(all(rank["top"] == 33 for rank in ranks))
        self.assertEqual(ranks[0]["label"], "Matcha Latte fans")

    def test_signature_order_combines_an_exact_repeated_basket(self):
        repeated = {
            "grand_total": 10.10,
            "clean_items": [
                {"item": "Fried Chicken Taco", "detail": "Hungry for More", "qty": 1, "total": 10.10},
            ],
        }
        other = {
            "grand_total": 5.25,
            "clean_items": [
                {"item": "Chick-fil-A Sandwich", "detail": "", "qty": 1, "total": 5.25},
            ],
        }
        usual = signature_order([repeated, other, repeated])
        self.assertEqual(usual["count"], 2)
        self.assertEqual(usual["spent"], 20.20)
        self.assertEqual(usual["items"][0]["detail"], "Hungry for More")

    def test_badges_are_rule_based_and_capped_at_three(self):
        transactions = [
            {
                "date": f"Oct {day}, 2026 09:00PM",
                "location": ["Dr Jacks", "CFA", "Tacos 1", "CFA", "Dr Jacks"][day - 1],
                "grand_total": 6.49,
                "clean_items": [{"item": "Matcha Latte", "detail": f"variation {day}", "qty": 1, "total": 6.49}],
            }
            for day in range(1, 6)
        ]
        badges = dining_badges(
            transactions,
            [{"date": "Oct 1, 2026 12:00PM", "location": "Commons PAX 1"}] * 25,
            {"startdate": "2026-07-01"},
        )
        self.assertEqual(len(badges), 3)
        self.assertEqual([badge["title"] for badge in badges], [
            "Matcha Machine", "Caf Regular", "Night Owl",
        ])

    def test_friend_meals_match_each_swipe_at_most_once(self):
        first = datetime(2026, 10, 18, 12, 5)
        second = datetime(2026, 10, 19, 17, 30)
        match = friend_match(
            {"items": {"Taco": 3}, "_caf_times": [first, second]},
            {"items": {"Taco": 1}, "_caf_times": [first, first, second]},
        )
        self.assertEqual(match["likely_meals"], 2)
        self.assertEqual(len(match["likely_meal_times"]), 2)


if __name__ == "__main__":
    unittest.main()
