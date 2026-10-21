import os
import tempfile
import threading
import unittest
from contextlib import closing

import jobs
import state


class JobTableTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = state.DB_FILE
        state.DB_FILE = os.path.join(self.tmp.name, "state.db")

    def tearDown(self):
        state.DB_FILE = self.original
        self.tmp.cleanup()

    def _count(self):
        with closing(state.connect()) as con:
            return con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    def test_parallel_starts_for_one_inbox_share_a_job(self):
        ids = []
        threads = [threading.Thread(target=lambda: ids.append(jobs.job_for("a" * 16))) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(self._count(), 1)

    def test_full_table_evicts_finished_and_stale_jobs_never_active_ones(self):
        original = jobs.MAX_JOBS
        jobs.MAX_JOBS = 3
        try:
            running = jobs.job_for("1" * 16)
            jobs.claim(running)
            done = jobs.job_for("2" * 16)
            jobs.finish(done, "x" * 12)
            waiting = jobs.job_for("3" * 16)

            fourth = jobs.job_for("4" * 16)  # the finished job makes room
            self.assertIsNotNone(fourth)
            self.assertIsNone(jobs.get_job(done))
            self.assertIsNotNone(jobs.get_job(running))

            self.assertIsNone(jobs.job_for("5" * 16))  # one running, two fresh waiting: full

            with closing(state.connect()) as con:
                con.execute("UPDATE jobs SET created_at = '2000-01-01T00:00:00' WHERE job_id = ?", (waiting,))
            self.assertIsNotNone(jobs.job_for("5" * 16))  # a stale waiting job makes room
            self.assertIsNotNone(jobs.get_job(running))
        finally:
            jobs.MAX_JOBS = original


if __name__ == "__main__":
    unittest.main()
