import html
import json
import os
import tempfile
import unittest

import analytics
import app
import jobs
import mailbox
import state


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = (app.RESULTS_DIR, app.ARCHIVE_DIR, mailbox.CREDENTIALS_DIR,
                         app.new_tag, app.OG_DIR, app.STATS_FILE, app._stats_cache,
                         analytics.DB_FILE, analytics.SALT_FILE, analytics._salt, state.DB_FILE)
        app.OG_DIR = os.path.join(self.tmp.name, "og")
        app.ARCHIVE_DIR = os.path.join(self.tmp.name, "archive")
        app.RESULTS_DIR = self.tmp.name
        app.STATS_FILE = os.path.join(self.tmp.name, "stats.json")
        app._stats_cache = {"mtime": object(), "stats": {}}
        mailbox.CREDENTIALS_DIR = os.path.join(self.tmp.name, "credentials")
        analytics.DB_FILE = os.path.join(self.tmp.name, "analytics.db")
        analytics.SALT_FILE = os.path.join(self.tmp.name, "analytics_salt")
        analytics._salt = None
        state.DB_FILE = os.path.join(self.tmp.name, "state.db")
        app.app.config["TESTING"] = True

    def tearDown(self):
        (app.RESULTS_DIR, app.ARCHIVE_DIR, mailbox.CREDENTIALS_DIR,
         app.new_tag, app.OG_DIR, app.STATS_FILE, app._stats_cache,
         analytics.DB_FILE, analytics.SALT_FILE, analytics._salt, state.DB_FILE) = self.original
        self.tmp.cleanup()

    def _events(self):
        con = analytics.connect()
        try:
            rows = con.execute("SELECT name, visitor, student, share_id, props FROM events ORDER BY id").fetchall()
        finally:
            con.close()
        return [{**dict(r), "props": json.loads(r["props"])} for r in rows]

    def _save_result(self, share_id, name="Pat Doe", student_id="12345", owner_token=None, owner_inbox=None):
        result = {"metadata": {"name": name, "student_id": student_id,
                               "startdate": "2026-07-01", "enddate": "2026-10-19",
                               "owner_hashes": [app._owner_hash(owner_token)] if owner_token else [],
                               "owner_visitor": analytics.visitor_id(owner_inbox) if owner_inbox else None},
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
        invite = {"username": "guest@example.com", "password": "p", "name": "Pat Doe",
                  "student_id": "12345", "email": "doe12345@obu.edu"}
        mailbox.save_credentials(tag, invite)
        client = app.app.test_client()
        client.set_cookie(app.INBOX_COOKIE, tag)

        first_visit = client.get("/").get_data(as_text=True)
        self.assertIn('data-action="initial-generate"', first_visit)
        self.assertNotIn('data-action="refresh-wrap"', first_visit)

        app._save_stats({"students": {"12345": {"name": "Pat Doe"}}})
        later_visit = client.get("/").get_data(as_text=True)
        self.assertIn('data-action="refresh-wrap"', later_visit)
        self.assertNotIn('data-action="initial-generate"', later_visit)

    def test_same_name_is_not_identity_and_student_rows_use_the_raw_id(self):
        self.assertFalse(app._same_student({"name": "Pat Doe", "student_id": "12345"},
                                           {"name": "Pat Doe", "student_id": "54321"}))
        self.assertTrue(app._same_student({"name": "Pat Doe", "student_id": "12345"},
                                          {"name": "Different Name", "student_id": "12345"}))
        analytics.save_student("12345", "doe12345@obu.edu", "Pat Doe")
        con = analytics.connect()
        try:
            row = con.execute("SELECT student_id, email, name FROM students").fetchone()
        finally:
            con.close()
        self.assertEqual(tuple(row), ("12345", "doe12345@obu.edu", "Pat Doe"))

    def test_wrap_persists_and_uses_the_atrium_id_end_to_end(self):
        tag = "abc123"
        job_id = jobs.job_for(tag, app._owner_hash("T" * 22))
        jobs.claim(job_id)
        invite = {"username": "guest@example.com", "password": "p", "name": "Drew Dettmer"}
        html_page = {
            "account": "Resident Meals", "acct": "2",
            "html": ('<aside aria-label="Customer Information"><span>Account</span>'
                     '<b>85124</b></aside>'),
        }
        swipes = [{"date": "Oct 19, 2026 08:21AM", "location": "Commons PAX 4"}]
        old_fetch, old_enabled = app.fetch_activity, app.analysis.enabled
        app.fetch_activity = lambda *_args, **_kwargs: ([], swipes, {"Resident Meals": 98}, [html_page])
        app.analysis.enabled = lambda: False
        try:
            app._wrap(job_id, tag, invite)
        finally:
            app.fetch_activity, app.analysis.enabled = old_fetch, old_enabled

        saved_job = jobs.get_job(job_id)
        self.assertEqual(saved_job["status"], "done")
        result = app._load_result(saved_job["share_id"])
        self.assertEqual(result["metadata"]["student_id"], "85124")
        self.assertEqual(result["metadata"]["email"], "det85124@obu.edu")
        self.assertEqual(mailbox.load_credentials(tag)["student_id"], "85124")
        self.assertIn("85124", app._load_stats()["students"])
        archive_path = os.path.join(app.ARCHIVE_DIR, os.listdir(app.ARCHIVE_DIR)[0])
        with open(archive_path, encoding="utf-8") as f:
            archive = json.load(f)
        self.assertEqual((archive["student_id"], archive["email"]),
                         ("85124", "det85124@obu.edu"))
        owner = app.app.test_client()
        owner.set_cookie(app.OWNER_COOKIE, "T" * 22)
        self.assertEqual(owner.get(f"/api/results/{saved_job['share_id']}").status_code, 200)

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

    def test_posted_link_source_is_recorded_on_landing(self):
        client = app.app.test_client()
        browser = {"User-Agent": "Mozilla/5.0 (iPhone) Safari"}
        client.get("/?src=YY1", headers=browser)
        client.get("/?src=<script>", headers=browser)
        tagged, junk = self._events()
        self.assertEqual(tagged["props"]["src"], "yy1")
        self.assertNotIn("src", junk["props"])

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
        self.assertEqual((slide["student"], slide["share_id"]), ("12345", "sharedwrapp1"))
        self.assertEqual(slide["props"], {"slide": "roast", "method": "share"})
        self.assertIsNone(link["share_id"])

    def test_private_routes_need_the_owner_token_or_the_runs_inbox_not_the_share_id(self):
        token = "T" * 22
        share_id = "abcdefghijkl"
        self._save_result(share_id, owner_token=token, owner_inbox="abc123")
        png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + (1080).to_bytes(4, "big") + (1920).to_bytes(4, "big")
        private = [lambda c: c.get(f"/api/results/{share_id}"), lambda c: c.get(f"/results/{share_id}"),
                   lambda c: c.get(f"/download/{share_id}/csv"),
                   lambda c: c.post(f"/api/slide-image/{share_id}/roast", data=png, content_type="image/png")]

        forger = app.app.test_client()
        forger.set_cookie(app.MINE_COOKIE, share_id)  # the public share id is not proof
        forger.set_cookie(app.OWNER_COOKIE, "U" * 22)
        forger.set_cookie(app.INBOX_COOKIE, "zzz999")
        self.assertEqual([call(forger).status_code for call in private], [404] * 4)

        by_token, by_inbox = app.app.test_client(), app.app.test_client()
        by_token.set_cookie(app.OWNER_COOKIE, token)
        by_inbox.set_cookie(app.INBOX_COOKIE, "abc123")
        self.assertEqual([call(by_token).status_code for call in private], [200, 200, 200, 204])
        self.assertEqual([call(by_inbox).status_code for call in private], [200, 200, 200, 204])

    def test_start_gives_the_browser_an_owner_token_that_its_finished_wrapped_honors(self):
        mailbox.save_credentials("abc123", {"username": "guest@example.com", "password": "p",
                                            "name": "Drew Dettmer"})
        started = []
        original = app._start_job
        app._start_job = lambda *args: started.append(args)
        try:
            client = app.app.test_client()
            client.set_cookie(app.INBOX_COOKIE, "abc123")
            client.post("/start")
        finally:
            app._start_job = original
        token = client.get_cookie(app.OWNER_COOKIE).value
        self.assertRegex(token, r"^[A-Za-z0-9_-]{22}$")
        job = jobs.get_job(started[0][0])
        self.assertEqual(job["owner_hash"], app._owner_hash(token))

    def test_running_jobs_restart_after_the_app_restarts(self):
        mailbox.save_credentials("abc123", {"username": "u", "password": "p", "name": "Pat Doe"})
        resumable, orphaned, waiting = jobs.job_for("abc123"), jobs.job_for("gone01"), jobs.job_for("wait01")
        jobs.claim(resumable)
        jobs.claim(orphaned)  # its saved login was deleted meanwhile
        started = []
        original = app._start_job
        app._start_job = lambda *args: started.append(args)
        try:
            app.recover_jobs()
        finally:
            app._start_job = original
        self.assertEqual([(job_id, tag) for job_id, tag, _ in started], [(resumable, "abc123")])
        self.assertEqual(jobs.get_job(orphaned)["status"], "error")
        self.assertEqual(jobs.get_job(waiting)["status"], "waiting")

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
        self.assertIsNone(app._meal_block({"balances": {"Voluntary Full Meals": 100},
                                           "weekly_meal_plans": ["Voluntary Full Meals"]}))

    def test_campus_flex_pace_needs_three_judged_students(self):
        two = [{"flex_short": True}, {"flex_short": False}, {"flex_short": None}, {}]
        self.assertIsNone(app._flex_campus(two))  # students without a Flex plan don't count
        self.assertEqual(app._flex_campus(two + [{"flex_short": True}]), 67)

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
        self._save_result("slideshare12", owner_token="T" * 22)
        png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + (1080).to_bytes(4, "big") + (1920).to_bytes(4, "big") + b"rest"
        upload = lambda client, body=png: client.post("/api/slide-image/slideshare12/roast", data=body,
                                                      content_type="image/png")
        friend, owner = app.app.test_client(), app.app.test_client()
        owner.set_cookie(app.OWNER_COOKIE, "T" * 22)
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

    def test_leaderboard_back_button_returns_to_the_wrapped_slide(self):
        self._save_result("backshare123")
        client = app.app.test_client()
        page = client.get("/campus?from=backshare123&at=rankings").get_data(as_text=True)
        self.assertIn('href="/wrapped/backshare123?at=rankings"', page)
        self.assertIn('const START_SLIDE = "rankings"', client.get("/wrapped/backshare123?at=rankings").get_data(as_text=True))
        # Unknown Wrappeds and slides fall back: home page, and the deck's first slide.
        self.assertIn('href="/">← Back to Stu Wrapped', client.get("/campus?from=nosuchshare1&at=rankings").get_data(as_text=True))
        self.assertIn('href="/wrapped/backshare123"', client.get("/campus?from=backshare123&at=evil").get_data(as_text=True))
        self.assertIn("const START_SLIDE = null", client.get("/wrapped/backshare123?at=evil").get_data(as_text=True))

    def test_open_in_safari_hands_the_inbox_over_once(self):
        instagram = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
                                   "(KHTML, like Gecko) Mobile/15E148 Instagram 350.0.0"}
        in_app = app.app.test_client()
        in_app.set_cookie(app.INBOX_COOKIE, "inst01")
        page = html.unescape(in_app.get("/", headers=instagram).get_data(as_text=True))
        self.assertIn("You're in Instagram's browser.", page)
        token = page.split('href="x-safari-https://localhost/h/', 1)[1].split('"', 1)[0]
        self.assertEqual(self._events()[-1]["props"]["inapp"], "Instagram")

        safari = app.app.test_client()
        safari.set_cookie(app.INBOX_COOKIE, "safar1")
        self.assertEqual(safari.get(f"/h/{token}").status_code, 302)
        self.assertEqual(safari.get_cookie(app.INBOX_COOKIE).value, "inst01")
        self.assertEqual(self._events()[-1]["name"], "browser_handoff")
        safari.set_cookie(app.INBOX_COOKIE, "safar1")
        safari.get(f"/h/{token}")  # single use
        self.assertEqual(safari.get_cookie(app.INBOX_COOKIE).value, "safar1")

        # A browser that already has its own saved Flex login keeps it.
        token = in_app.get("/", headers=instagram).get_data(as_text=True).split("/h/", 1)[1].split('"', 1)[0]
        mailbox.save_credentials("safar1", {"username": "u", "password": "p", "name": "Pat Doe"})
        safari.get(f"/h/{token}")
        self.assertEqual(safari.get_cookie(app.INBOX_COOKIE).value, "safar1")

        android = {"User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8 Build/AP2A; wv) AppleWebKit/537.36 "
                                 "(KHTML, like Gecko) Version/4.0 Chrome/129.0 Mobile Safari/537.36"}
        page = html.unescape(in_app.get("/", headers=android).get_data(as_text=True))
        self.assertIn('href="intent://localhost/h/', page)
        self.assertIn("You're in an in-app browser.", page)
        real_safari = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
                                     "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"}
        self.assertNotIn("/h/", in_app.get("/", headers=real_safari).get_data(as_text=True))

    def test_yik_yak_links_on_iphone_safari_get_the_open_in_safari_offer(self):
        # Yik Yak's Safari view sends Safari's exact user agent; the ?src=yy… tag is the tell.
        safari_ua = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
                                   "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"}
        chrome_ios = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
                                    "(KHTML, like Gecko) CriOS/129.0 Mobile/15E148 Safari/604.1"}
        client = app.app.test_client()
        client.set_cookie(app.INBOX_COOKIE, "yak001")
        page = html.unescape(client.get("/?src=yy3", headers=safari_ua).get_data(as_text=True))
        self.assertIn("Opened this in Yik Yak?", page)
        token = page.split('href="x-safari-https://localhost/h/', 1)[1].split('"', 1)[0]
        self.assertEqual(self._events()[-1]["props"].get("safari_view_guess"), "Yik Yak")
        self.assertNotIn("inapp", self._events()[-1]["props"])  # analytics keeps detected apps only
        self.assertNotIn("/h/", client.get("/?src=yy3", headers=chrome_ios).get_data(as_text=True))
        self.assertNotIn("/h/", client.get("/?src=ig", headers=safari_ua).get_data(as_text=True))

        safari = app.app.test_client()
        safari.get(f"/h/{token}")
        self.assertEqual(safari.get_cookie(app.INBOX_COOKIE).value, "yak001")


if __name__ == "__main__":
    unittest.main()
