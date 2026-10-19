import json
import os
import tempfile
import unittest

import analytics
import app
import jobs
import mailbox


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = (app.RESULTS_DIR, mailbox.CREDENTIALS_DIR, app.new_tag, app.OG_DIR,
                         app.STATS_FILE, app._stats_cache,
                         analytics.DB_FILE, analytics.SALT_FILE, analytics._salt)
        app.OG_DIR = os.path.join(self.tmp.name, "og")
        app.RESULTS_DIR = self.tmp.name
        app.STATS_FILE = os.path.join(self.tmp.name, "stats.json")
        app._stats_cache = {"mtime": object(), "stats": {}}
        mailbox.CREDENTIALS_DIR = os.path.join(self.tmp.name, "credentials")
        analytics.DB_FILE = os.path.join(self.tmp.name, "analytics.db")
        analytics.SALT_FILE = os.path.join(self.tmp.name, "analytics_salt")
        analytics._salt = None
        app.app.config["TESTING"] = True
        jobs.JOBS.clear()

    def tearDown(self):
        (app.RESULTS_DIR, mailbox.CREDENTIALS_DIR, app.new_tag, app.OG_DIR,
         app.STATS_FILE, app._stats_cache,
         analytics.DB_FILE, analytics.SALT_FILE, analytics._salt) = self.original
        self.tmp.cleanup()

    def _events(self):
        con = analytics.connect()
        try:
            rows = con.execute("SELECT name, visitor, student, share_id, props FROM events ORDER BY id").fetchall()
        finally:
            con.close()
        return [{**dict(r), "props": json.loads(r["props"])} for r in rows]

    def _save_result(self, share_id, name="Pat Doe", key="k" * 16):
        result = {"metadata": {"name": name, "student_key": key,
                               "startdate": "2026-07-01", "enddate": "2026-10-19"},
                  "transactions": [], "swipes": []}
        with open(os.path.join(self.tmp.name, f"{share_id}.json"), "w", encoding="utf-8") as f:
            json.dump(result, f)

    def _inbound(self, sender: str, extra_headers: str = ""):
        raw = (f"From: Atrium <{sender}>\r\nTo: abc123@stu.exposed\r\n{extra_headers}"
               "Subject: Guest Access\r\nContent-Type: text/plain\r\n\r\n"
               "Name: Pat Doe\nPassword: hunter2\n").encode()
        os.environ["CREDENTIAL_SECRET"] = "test-secret"
        return app.app.test_client().post("/api/inbound", data=raw, headers={
            "X-Credential-Secret": "test-secret", "X-Envelope-To": "abc123@stu.exposed",
            "Content-Type": "message/rfc822"})

    def test_only_atrium_invites_set_a_saved_login(self):
        self.assertEqual(self._inbound("someone@example.com").status_code, 422)
        spoofed = "Authentication-Results: mx.cloudflare.net; dmarc=fail header.from=atriumcampus.com\r\n"
        self.assertEqual(self._inbound("noreply@atriumcampus.com", spoofed).status_code, 422)
        self.assertIsNone(mailbox.load_credentials("abc123"))
        self.assertEqual(self._inbound("NoReply@AtriumCampus.com").status_code, 200)
        self.assertEqual(mailbox.load_credentials("abc123")["password"], "hunter2")

    def test_new_inboxes_are_six_characters_and_skip_saved_logins(self):
        self.assertRegex(mailbox.new_tag(), r"^[a-z0-9]{6}$")
        mailbox.save_credentials("taken1", {"username": "u", "password": "p", "name": None})
        issued = iter(["taken1", "fresh1"])
        app.new_tag = lambda: next(issued)
        client = app.app.test_client()
        client.get("/")
        self.assertEqual(client.get_cookie(app.INBOX_COOKIE).value, "fresh1")

    def test_saved_login_without_a_prior_run_gets_the_first_run_action(self):
        tag = "abc123"
        invite = {"username": "guest@example.com", "password": "p", "name": "Pat Doe"}
        mailbox.save_credentials(tag, invite)
        client = app.app.test_client()
        client.set_cookie(app.INBOX_COOKIE, tag)

        first_visit = client.get("/").get_data(as_text=True)
        self.assertIn('data-action="initial-generate"', first_visit)
        self.assertNotIn('data-action="refresh-wrap"', first_visit)

        student_key = app._student_key(invite, tag)
        app._save_stats({"students": {student_key: {"name": "Pat Doe"}}})
        later_visit = client.get("/").get_data(as_text=True)
        self.assertIn('data-action="refresh-wrap"', later_visit)
        self.assertNotIn('data-action="initial-generate"', later_visit)

    def test_page_visits_extend_saved_cookies_to_the_400_day_cap(self):
        client = app.app.test_client()
        client.set_cookie(app.MINE_COOKIE, "abcdefghijkl")
        page = client.get("/")
        mine = [h for h in page.headers.getlist("Set-Cookie") if h.startswith("mine=")]
        self.assertEqual(len(mine), 1)
        self.assertIn(f"Max-Age={400 * 24 * 3600}", mine[0])
        asset = client.get("/static/css/main.css")
        self.assertEqual(asset.headers.getlist("Set-Cookie"), [])

    def test_friend_link_landing_and_signup_share_one_visitor(self):
        self._save_result("friendshare1")
        client = app.app.test_client()
        client.set_cookie(app.INBOX_COOKIE, "abc123")
        browser = {"User-Agent": "Mozilla/5.0 (iPhone) Safari"}
        client.get("/?vs=friendshare1", headers=browser)
        client.get("/", headers={"User-Agent": "Discordbot/2.0"})  # bots are not visitors
        self.assertEqual(self._inbound("noreply@atriumcampus.com").status_code, 200)
        landing, invite = self._events()
        self.assertEqual(landing["name"], "landing_view")
        self.assertTrue(landing["props"]["from_friend"])
        self.assertEqual(landing["share_id"], "friendshare1")
        self.assertEqual(invite["name"], "invite_received")
        self.assertEqual(invite["props"]["name"], "Pat Doe")
        self.assertEqual(invite["visitor"], landing["visitor"])
        self.assertNotIn("abc123", json.dumps(self._events()))  # the tag itself is never stored

    def test_browser_events_are_allowlisted(self):
        self._save_result("sharedwrapp1")
        client = app.app.test_client()
        send = lambda body: client.post("/api/event", data=json.dumps(body), content_type="application/json")
        self.assertEqual(send({"name": "drop_table"}).status_code, 400)
        self.assertEqual(send({"name": "share_slide", "method": "teleport"}).status_code, 400)
        self.assertEqual(send({"name": "share_slide", "share_id": "sharedwrapp1", "slide": "roast",
                               "method": "share", "extra": "ignored"}).status_code, 204)
        self.assertEqual(send({"name": "share_link", "share_id": "nosuchshare1", "method": "copy"}).status_code, 204)
        slide, link = self._events()
        self.assertEqual((slide["student"], slide["share_id"]), ("k" * 16, "sharedwrapp1"))
        self.assertEqual(slide["props"], {"slide": "roast", "method": "share"})
        self.assertIsNone(link["share_id"])

    def test_raw_receipts_are_only_served_to_the_owner(self):
        share_id = "abcdefghijkl"
        self._save_result(share_id)
        client = app.app.test_client()
        self.assertEqual(client.get(f"/api/results/{share_id}").status_code, 404)
        client.set_cookie(app.MINE_COOKIE, share_id)
        self.assertEqual(client.get(f"/api/results/{share_id}").status_code, 200)

    def test_unexpected_error_after_login_fails_the_job_instead_of_hanging(self):
        tag = "b" * 16
        job_id = jobs.job_for(tag)
        jobs.claim(job_id)
        original = app._wrap

        def crash(*_args):
            raise OSError("disk full")

        app._wrap = crash
        try:
            app._run_job(job_id, tag, {})
        finally:
            app._wrap = original
        self.assertEqual(jobs.get_job(job_id)["status"], "error")

    def test_split_last_purchase_and_out_of_flex(self):
        # $20 of Flex: a $15 meal, then a $14.70 meal that Flex only covered $5 of.
        purchases = [
            {"date": "Sep 9, 2026 12:36PM", "amount": -5.0, "grand_total": 14.70, "balance_after": 0.0},
            {"date": "Sep 1, 2026 12:00PM", "amount": -15.0, "grand_total": 15.0, "balance_after": 5.0},
        ]
        swipes = [{"date": "Sep 8, 2026 08:00AM"}, {"date": "Sep 10, 2026 08:00AM"}, {"date": "Oct 1, 2026 08:00AM"}]
        meta = {"startdate": "2026-07-01", "enddate": "2026-10-19",
                "balances": {"Commuter Flex": 0.0, "Basic Meals": 26.0}}
        plan = app._flex_plan(purchases, meta, swipes)
        self.assertEqual((plan["ran_out"], plan["started_with"], plan["lasted_days"], plan["swipes_since"]),
                         ("September 9", 20.0, 9, 2))
        self.assertIsNone(plan.get("runs_out"))
        self.assertEqual(app.summary_highlights(purchases, swipes, meta)["flex_spend"], 20.0)
        self.assertEqual(app._meal_block(meta), {"name": "Basic Meals", "left": 26})
        self.assertIsNone(app._meal_block({"balances": {"Resident Meals": 98}}))

    def test_tiger_express_purchases_stay_off_the_leaderboard(self):
        buy = lambda where, total, item: {"date": "Sep 2, 2026 12:10PM", "location": where, "amount": -total,
                                          "grand_total": total, "items": [],
                                          "clean_items": [{"item": item, "detail": "", "qty": 1, "total": total}]}
        summary = app._student_summary([buy("CFA 1", 5.25, "Chick-fil-A Sandwich"),
                                        buy("Tiger Exp 1", 3.89, "Propel")], [])
        self.assertEqual((summary["total_spend"], summary["visits"]), (5.25, 1))
        self.assertNotIn("Propel", summary["items"])
        self.assertNotIn("Tiger Express", summary["spots"])

    def test_slide_links_preview_the_owners_uploaded_slide(self):
        self._save_result("slideshare12")
        png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + (1080).to_bytes(4, "big") + (1920).to_bytes(4, "big") + b"rest"
        upload = lambda client, body=png: client.post("/api/slide-image/slideshare12/roast", data=body,
                                                      content_type="image/png")
        friend, owner = app.app.test_client(), app.app.test_client()
        owner.set_cookie(app.MINE_COOKIE, "slideshare12")
        self.assertEqual(upload(friend).status_code, 404)
        self.assertEqual(upload(owner, b"not a png" * 4).status_code, 400)
        before = friend.get("/wrapped/slideshare12/roast").get_data(as_text=True)
        self.assertIn("/og/slideshare12.png", before)  # no slide image yet: the Wrapped's card
        self.assertEqual(upload(owner).status_code, 204)
        page = friend.get("/wrapped/slideshare12/roast").get_data(as_text=True)
        self.assertIn('content="https://localhost/og/slideshare12/roast.png"', page)
        self.assertIn('og:image:height" content="1920"', page)
        self.assertIn("Get your own Wrapped", page)
        self.assertNotIn('id="share-btn"', page)
        self.assertEqual(friend.get("/og/slideshare12/roast.png").data, png)
        self.assertEqual(friend.get("/wrapped/slideshare12/nonsense").status_code, 302)


if __name__ == "__main__":
    unittest.main()
