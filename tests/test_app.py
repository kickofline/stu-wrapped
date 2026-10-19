import json
import os
import tempfile
import unittest

import app
import jobs
import mailbox


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = (app.RESULTS_DIR, mailbox.CREDENTIALS_DIR, app.new_tag)
        app.RESULTS_DIR = self.tmp.name
        mailbox.CREDENTIALS_DIR = os.path.join(self.tmp.name, "credentials")
        app.app.config["TESTING"] = True
        jobs.JOBS.clear()

    def tearDown(self):
        app.RESULTS_DIR, mailbox.CREDENTIALS_DIR, app.new_tag = self.original
        self.tmp.cleanup()

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

    def test_page_visits_extend_saved_cookies_to_the_400_day_cap(self):
        client = app.app.test_client()
        client.set_cookie(app.MINE_COOKIE, "abcdefghijkl")
        page = client.get("/")
        mine = [h for h in page.headers.getlist("Set-Cookie") if h.startswith("mine=")]
        self.assertEqual(len(mine), 1)
        self.assertIn(f"Max-Age={400 * 24 * 3600}", mine[0])
        asset = client.get("/static/css/main.css")
        self.assertEqual(asset.headers.getlist("Set-Cookie"), [])

    def test_raw_receipts_are_only_served_to_the_owner(self):
        share_id = "abcdefghijkl"
        result = {"metadata": {"name": "Pat Doe", "student_key": "k" * 16,
                               "startdate": "2026-07-01", "enddate": "2026-10-19"},
                  "transactions": [], "swipes": []}
        with open(os.path.join(self.tmp.name, f"{share_id}.json"), "w", encoding="utf-8") as f:
            json.dump(result, f)
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


if __name__ == "__main__":
    unittest.main()
