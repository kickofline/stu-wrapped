import json
import os
import tempfile
import unittest

import analytics
import app
import mailbox
from tools import backfill_student_identity as backfill


class BackfillIdentityTests(unittest.TestCase):
    def test_archives_drive_an_idempotent_full_data_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = (app.DATA_DIR, app.ARCHIVE_DIR, app.RESULTS_DIR, app.STATS_FILE,
                        mailbox.CREDENTIALS_DIR, analytics.DB_FILE, analytics.SALT_FILE,
                        analytics._salt)
            app.DATA_DIR = tmp
            app.ARCHIVE_DIR = os.path.join(tmp, "archive")
            app.RESULTS_DIR = os.path.join(tmp, "results")
            app.STATS_FILE = os.path.join(tmp, "stats.json")
            mailbox.CREDENTIALS_DIR = os.path.join(tmp, "credentials")
            analytics.DB_FILE = os.path.join(tmp, "analytics.db")
            analytics.SALT_FILE = os.path.join(tmp, "analytics_salt")
            analytics._salt = None
            for directory in (app.ARCHIVE_DIR, app.RESULTS_DIR, mailbox.CREDENTIALS_DIR):
                os.makedirs(directory)
            old_key, tag, run_id = "legacy-name-hash", "abc123", "run-one"
            html = ('<aside aria-label="Customer Information"><span>Account</span>'
                    '<b>85124</b></aside>')
            archive_path = os.path.join(app.ARCHIVE_DIR, "run.json")
            result_path = os.path.join(app.RESULTS_DIR, "abcdefghijkl.json")
            credential_path = os.path.join(mailbox.CREDENTIALS_DIR, f"{tag}.json")
            fixtures = {
                archive_path: {"run_id": run_id, "inbox": tag, "name": "Drew Dettmer",
                               "student_key": old_key, "pages": [{"html": html}]},
                result_path: {"metadata": {"run_id": run_id, "name": "Drew Dettmer",
                                            "student_key": old_key}},
                credential_path: {"name": "Drew Dettmer", "username": "guest", "password": "p"},
                app.STATS_FILE: {"students": {old_key: {"updated": "2026-10-20T12:00:00"}}},
            }
            for path, value in fixtures.items():
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(value, f)
            analytics.record("wrap_finished", visitor=analytics.visitor_id(tag), student=old_key)

            try:
                plan = backfill._build_plan()
                self.assertEqual(plan["errors"], [])
                backup = backfill._apply(plan)
                self.assertTrue(os.path.exists(os.path.join(backup, "analytics.db")))
                for path in (archive_path, credential_path):
                    with open(path, encoding="utf-8") as f:
                        saved = json.load(f)
                    self.assertEqual((saved["student_id"], saved["email"]),
                                     ("85124", "det85124@obu.edu"))
                    self.assertNotIn("student_key", saved)
                with open(result_path, encoding="utf-8") as f:
                    meta = json.load(f)["metadata"]
                self.assertEqual(meta["student_id"], "85124")
                with open(app.STATS_FILE, encoding="utf-8") as f:
                    self.assertEqual(list(json.load(f)["students"]), ["85124"])
                con = analytics.connect()
                try:
                    student = con.execute("SELECT student_id, email FROM students").fetchone()
                    event_student = con.execute("SELECT student FROM events").fetchone()[0]
                finally:
                    con.close()
                self.assertEqual(tuple(student), ("85124", "det85124@obu.edu"))
                self.assertEqual(event_student, "85124")
                rerun = backfill._build_plan()
                self.assertEqual(rerun["errors"], [])
                self.assertFalse(rerun["stats_changed"])
                self.assertEqual((rerun["archives"], rerun["results"], rerun["credentials"]),
                                 ({}, {}, {}))
            finally:
                (app.DATA_DIR, app.ARCHIVE_DIR, app.RESULTS_DIR, app.STATS_FILE,
                 mailbox.CREDENTIALS_DIR, analytics.DB_FILE, analytics.SALT_FILE,
                 analytics._salt) = original


if __name__ == "__main__":
    unittest.main()
