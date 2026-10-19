import threading
import unittest
from datetime import timedelta

import jobs


class JobTableTests(unittest.TestCase):
    def setUp(self):
        jobs.JOBS.clear()

    def test_parallel_starts_for_one_inbox_share_a_job(self):
        ids = []
        threads = [threading.Thread(target=lambda: ids.append(jobs.job_for("a" * 16))) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(len(jobs.JOBS), 1)

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
            self.assertNotIn(done, jobs.JOBS)
            self.assertIn(running, jobs.JOBS)

            self.assertIsNone(jobs.job_for("5" * 16))  # one running, two fresh waiting: full

            jobs.JOBS[waiting]["created_at"] -= timedelta(hours=1)
            self.assertIsNotNone(jobs.job_for("5" * 16))  # a stale waiting job makes room
            self.assertIn(running, jobs.JOBS)
        finally:
            jobs.MAX_JOBS = original


if __name__ == "__main__":
    unittest.main()
