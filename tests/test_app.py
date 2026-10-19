import json
import os
import tempfile
import unittest

import app
import jobs


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_results = app.RESULTS_DIR
        app.RESULTS_DIR = self.tmp.name
        app.app.config["TESTING"] = True
        jobs.JOBS.clear()

    def tearDown(self):
        app.RESULTS_DIR = self.original_results
        self.tmp.cleanup()

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
