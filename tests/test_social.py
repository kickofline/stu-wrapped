import unittest
from datetime import datetime, timedelta

from social import (campus_medals, campus_percentiles, campus_wrapped, dining_badges, dining_streaks,
                    friend_match, nuggets_eaten, signature_order, summary_highlights, tiger_express)


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

    def test_medals_for_top_three_with_shared_places_and_a_minimum(self):
        swipes = [100, 100, 90, 80, 70, 0]
        students = [{"caf_swipes": s, "nuggets": 50 if i < 4 else 0} for i, s in enumerate(swipes)]
        tied_first = campus_medals(students[0], students)
        self.assertEqual([(m["rank"], m["label"]) for m in tied_first], [("1st", "Caf swipes")])
        self.assertEqual(campus_medals(students[2], students)[0]["rank"], "3rd")  # two firsts, then third
        self.assertEqual(campus_medals(students[3], students), [])  # 4th: no medal
        self.assertEqual(campus_medals(students[5], students), [])  # none of it: no medal
        # Nuggets: only four students have any, under the five-student minimum.
        self.assertNotIn("Nuggets eaten", [m["label"] for m in campus_medals(students[0], students)])

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
        swipe = {"date": "Oct 1, 2026 12:00PM", "location": "Commons PAX 1"}
        meta = {"startdate": "2026-07-01"}
        badges = dining_badges(transactions, [swipe] * 25, meta)
        self.assertEqual([badge["title"] for badge in badges], [
            "Matcha Machine", "Night Owl", "Campus Explorer",
        ])  # 25 swipes is ordinary; a Caf Regular has 80+
        self.assertEqual([badge["title"] for badge in dining_badges(transactions, [swipe] * 80, meta)][:2],
                         ["Matcha Machine", "Caf Regular"])

    def test_habit_badges_breakfast_second_dinner_and_energy_flavors(self):
        breakfast = [{"date": f"Sep {d}, 2026 07:30AM", "location": "Commons PAX 1"} for d in range(1, 21)]
        breakfast.append({"date": "Sep 1, 2026 08:45AM", "location": "Commons PAX 2"})  # same morning
        dinner = [{"date": f"Sep {d}, 2026 05:30PM", "location": "Commons PAX 4"} for d in range(14, 20)]
        after_dinner = [{"date": f"Sep {d}, 2026 06:15PM", "location": "CFA 1", "grand_total": 5.65,
                         "clean_items": [{"item": "Spicy Chicken Sandwich", "detail": "", "qty": 1, "total": 5.65}]}
                        for d in range(14, 20)]
        flavors = ["Cotton Candy", "Cotton Candy, 24 oz", "Cosmic Star", "Hawaiian"]
        energy = [{"date": f"Sep {d}, 2026 02:00PM", "location": "C-Store", "grand_total": 2.99,
                   "clean_items": [{"item": "Alani Nu", "detail": flavors[d % 4], "qty": 1, "total": 2.99}]}
                  for d in range(21, 29)]
        badges = dining_badges(after_dinner + energy, breakfast + dinner, {"startdate": "2026-07-01"})
        self.assertEqual([(b["title"], b["description"]) for b in badges], [
            ("Energy Drink Era", "8 energy drinks across 3 flavors."),
            ("Second Dinner", "6 food runs right after a Caf meal."),
            ("Breakfast Club", "20 mornings eating before 10:30 AM."),
        ])

    def test_nuggets_eaten_counts_each_order_and_skips_minis(self):
        orders = [{"clean_items": [{"item": "Chick-fil-A Nuggets (12 ct)", "detail": "Meal", "qty": 2, "total": 20},
                                   {"item": "Chick-n-Minis (4 ct)", "detail": "", "qty": 1, "total": 5}]},
                  {"clean_items": [{"item": "Chick-fil-A Grilled Nuggets (8 ct)", "detail": "", "qty": 1, "total": 6}]}]
        self.assertEqual(nuggets_eaten(orders), 32)

    def test_friend_meals_pair_swipes_within_one_minute_once_each(self):
        noon = datetime(2026, 10, 18, 12, 5)
        dinner = datetime(2026, 10, 19, 17, 30)
        match = friend_match(
            {"items": {"Taco": 3}, "_caf_times": [noon, dinner, datetime(2026, 10, 20, 8, 0)]},
            # Two swipes near noon (only one can pair), dinner 1 min late (pairs),
            # and one 2 min off that must not pair.
            {"items": {"Taco": 1}, "_caf_times": [
                noon, noon.replace(minute=6), dinner.replace(minute=31),
                datetime(2026, 10, 20, 8, 2),
            ]},
        )
        self.assertEqual(match["likely_meals"], 2)
        self.assertIsNone(match["meal_title"])

    def test_friend_meal_titles_start_at_ten_and_thirty(self):
        def title(count):
            times = [datetime(2026, 9, 1, 12) + timedelta(days=day) for day in range(count)]
            return friend_match({"_caf_times": times}, {"_caf_times": times})["meal_title"]
        self.assertIsNone(title(9))
        self.assertEqual(title(10), "Caf Crew")
        self.assertEqual(title(29), "Caf Crew")
        self.assertEqual(title(40), "Caf Soulmates")

    def test_summary_highlights_replace_restaurant_rankings(self):
        transactions = [
            {
                "date": "Oct 5, 2026 12:00PM", "location": "CFA", "grand_total": 5,
                "clean_items": [{"item": "Sandwich", "detail": "", "qty": 1, "total": 5}],
            },
            {
                "date": "Oct 5, 2026 06:00PM", "location": "Dr Jacks", "grand_total": 9,
                "clean_items": [{"item": "Burger", "detail": "", "qty": 1, "total": 9}],
            },
            {
                "date": "Oct 6, 2026 12:00PM", "location": "Tacos 1", "grand_total": 20,
                "clean_items": [
                    {"item": "Tacos", "detail": "", "qty": 2, "total": 16},
                    {"item": "Drink", "detail": "", "qty": 1, "total": 4},
                ],
            },
        ]
        swipes = [
            {"date": "Oct 5, 2026 12:15PM", "location": "Commons PAX 1"},
            {"date": "Oct 5, 2026 12:30PM", "location": "Commons PAX 1"},
        ]

        summary = summary_highlights(
            transactions, swipes, {"startdate": "2026-07-01"},
        )

        self.assertEqual(summary["biggest_day"], {
            "date": "Oct 6", "amount": 20, "visits": 1,
        })
        self.assertEqual(summary["biggest_order"]["item"], "Tacos")
        self.assertEqual(summary["biggest_order"]["more_items"], 1)
        self.assertEqual(summary["routine"]["weekday"], "Monday")
        self.assertEqual(summary["routine"]["meal"], "Lunch")
        self.assertEqual(
            (summary["caf_swipes"], summary["flex_visits"], summary["flex_spend"]),
            (2, 3, 34),
        )

    def test_streaks_count_consecutive_days_and_need_three(self):
        meta = {"startdate": "2026-07-01"}
        swipe = lambda day: {"date": f"Sep {day}, 2026 12:00PM", "location": "Commons PAX 4"}
        buy = lambda day: {"date": f"Sep {day}, 2026 06:00PM", "location": "CFA 1", "grand_total": 5}
        # Caf on 8-11 (4 days), a gap, Caf on 20-21; a Flex purchase on 12 extends the food streak.
        swipes = [swipe(d) for d in (8, 9, 10, 11, 20, 21)] + [swipe(9)]
        streaks = dining_streaks([buy(12)], swipes, meta)
        self.assertEqual(streaks["caf"], {"days": 4, "span": "Sep 8–11"})
        self.assertEqual(streaks["food"], {"days": 5, "span": "Sep 8–12"})
        self.assertIsNone(dining_streaks([], [swipe(8), swipe(9)], meta))

    def test_campus_wrapped_hides_anything_fewer_than_three_students_share(self):
        def student(items, spots):
            return {"total_spend": 10, "visits": 2, "caf_swipes": 3, "items": items, "spots": spots,
                    "weekdays": [1, 0, 0, 0, 0, 0, 0], "hours": [0] * 12 + [1] + [0] * 11, "caf_streak": 2}
        students = [student({"Taco": 2, "Rare Item": 9}, {"Tacos 4 Life": 2}) for _ in range(2)]
        self.assertIsNone(campus_wrapped(students))
        students.append(student({"Taco": 1}, {"Tacos 4 Life": 1}))
        campus = campus_wrapped(students)
        self.assertEqual([i["name"] for i in campus["top_items"]], ["Taco"])  # Rare Item: only 2 students
        self.assertEqual(campus["top_items"][0]["count"], 5)
        self.assertEqual(campus["busiest"]["day"], "Monday")
        self.assertEqual(campus["busiest"]["hour"], "12 PM")
        students[0].pop("weekdays")  # runs saved before activity was stored don't count toward it
        self.assertIsNone(campus_wrapped(students)["busiest"])

    def test_tiger_express_needs_two_swipes_and_badges_count_and_habit(self):
        meta = {"startdate": "2026-07-01"}
        caf = [{"date": f"Sep {day}, 2026 12:00PM", "location": "Commons PAX 4"} for day in range(1, 11)]
        tiger = lambda day: {"date": f"Sep {day}, 2026 12:20PM", "location": "Tiger Exp 1"}
        self.assertIsNone(tiger_express(caf + [tiger(1)], meta))
        # Tuesdays Sep 1, 8, 15 plus Thursday Sep 3: 4 swipes, 3 on Tuesday.
        info = tiger_express(caf + [tiger(d) for d in (1, 3, 8, 15)], meta)
        self.assertEqual(info["count"], 4)
        self.assertEqual(info["share"], 29)  # 4 of 14 Caf swipes
        self.assertEqual((info["day"], info["hour"]), ("Tuesday", "12 PM"))
        self.assertEqual([b["title"] for b in info["badges"]], ["Grab & Go", "Tuesday Ritual"])
        spread = tiger_express([tiger(d) for d in range(1, 6)], meta)  # Tue-Sat, one each
        self.assertEqual([b["title"] for b in spread["badges"]], ["Express Lane"])
        many = tiger_express([tiger(d) for d in range(1, 11)], meta)
        self.assertEqual(many["badges"][0]["title"], "Tiger Express VIP")

    def test_tiger_express_is_not_a_favorite_spot(self):
        from insights import time_insights
        buys = [{"date": "Sep 1, 2026 12:00PM", "location": "CFA 1"},
                {"date": "Sep 2, 2026 12:10PM", "location": "Tiger Exp 1"}]
        times = time_insights(buys, "2026-07-01", "2026-10-19")
        self.assertEqual({spot["location"] for group in times["spots"] for spot in group["top"]}, {"CFA"})
        self.assertEqual(times["count"], 2)  # still a purchase, just not a spot


if __name__ == "__main__":
    unittest.main()
