import unittest

from scraper import parse_swipes

# Trimmed from a real Resident Meals statement: a Caf swipe, a meal exchange at another
# counter (itemized receipt inside <details>), and the weekly positive reset.
MEAL_STATEMENT = """
<table><tbody>
<tr><th class="jsa_month">Oct 19, 2026 08:21AM</th>
  <td class="jsa_desc">
    Commons PAX 4
  </td>
  <td class="jsa_amount neg">-1 <span>Transaction Balance</span> 98</td></tr>
<tr><th class="jsa_month">Sep 29, 2026 12:23PM</th>
  <td class="jsa_desc"><details><summary style="display:list-item;">Tiger Exp 1</summary>
    <div role="region"><div><b>Transaction ID:</b> 1982083</div>
    <table class="jsa_transactions"><tbody>
      <tr role="row"><td headers="item">Grill Meal Exchange</td><td headers="subtotal">1</td></tr>
    </tbody></table></div></details></td>
  <td class="jsa_amount neg">-1 <span>Transaction Balance</span> 97</td></tr>
<tr><th class="jsa_month">Sep 28, 2026 12:00AM</th>
  <td class="jsa_desc">Automated Reset</td>
  <td class="jsa_amount">14 <span>Transaction Balance</span> 98</td></tr>
</tbody></table>
"""


class ScraperTests(unittest.TestCase):
    def test_meal_swipes_use_the_counter_name_and_skip_resets(self):
        self.assertEqual(parse_swipes(MEAL_STATEMENT), [
            {"date": "Oct 19, 2026 08:21AM", "location": "Commons PAX 4"},
            {"date": "Sep 29, 2026 12:23PM", "location": "Tiger Exp 1"},
        ])


if __name__ == "__main__":
    unittest.main()
