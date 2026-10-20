import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import scraper
from scraper import parse_swipes, weekly_meal_plans

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

    def test_only_meal_plans_atrium_refills_count_as_weekly(self):
        block = MEAL_STATEMENT.replace("Automated Reset", "Deposit")
        pages = [{"account": "Voluntary Full Meals", "html": MEAL_STATEMENT},
                 {"account": "Basic Meals", "html": block},
                 {"account": "Resident Flex", "html": MEAL_STATEMENT}]
        self.assertEqual(weekly_meal_plans(pages), ["Voluntary Full Meals"])

    def test_page_loads_retry_atrium_server_errors_but_the_login_post_does_not(self):
        hits = {"GET": 0, "POST": 0}

        class FlakyAtrium(BaseHTTPRequestHandler):
            def _answer(self):
                hits[self.command] += 1
                self.send_response(500 if hits[self.command] == 1 else 200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_GET = do_POST = _answer

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), FlakyAtrium)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_port}/login.php"
        try:
            with scraper._session() as session:
                session.mount("http://", session.get_adapter("https://"))
                self.assertEqual(session.get(url, timeout=5).status_code, 200)
                self.assertEqual(session.post(url, timeout=5).status_code, 500)
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(hits, {"GET": 2, "POST": 1})


if __name__ == "__main__":
    unittest.main()
