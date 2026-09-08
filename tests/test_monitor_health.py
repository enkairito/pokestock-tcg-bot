import unittest
from datetime import datetime, timezone
from monitor_health import evaluate


class HealthTests(unittest.TestCase):
    now = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)

    def run_record(self, conclusion="success", stamp="2026-09-08T11:00:00Z"):
        return {"status": "completed", "conclusion": conclusion, "updated_at": stamp}

    def test_valid_sold_out_snapshot_is_healthy(self):
        self.assertEqual(evaluate([self.run_record()], {"updated_at": "2026-09-08T11:00:00Z", "products": []}, 1, self.now), [])

    def test_successful_workflow_does_not_hide_stale_publication(self):
        self.assertTrue(evaluate([self.run_record()], {"updated_at": "2026-09-08T08:00:00Z", "products": []}, 1, self.now))

    def test_recent_success_allows_a_bounded_deployment_window(self):
        snapshot = {"updated_at": "2026-09-08T08:00:00Z", "products": []}
        self.assertEqual(evaluate([self.run_record(stamp="2026-09-08T11:59:00Z")], snapshot, 1, self.now), [])
        self.assertTrue(evaluate([self.run_record(stamp="2026-09-08T11:54:00Z")], snapshot, 1, self.now))

    def test_cadence_and_missing_dates(self):
        snapshot = {"updated_at": "2026-09-08T08:00:00Z", "products": []}
        self.assertEqual(evaluate([self.run_record()], snapshot, 6, self.now), [])
        for stamp in (None, "bad-date", "2026-09-09T12:00:00Z"):
            self.assertTrue(evaluate([self.run_record()], {**snapshot, "updated_at": stamp}, 6, self.now))

    def test_running_workflow_does_not_mask_repeated_failures(self):
        runs = [{"status": "in_progress"}, self.run_record("failure"), self.run_record("timed_out"), self.run_record()]
        problems = evaluate(runs, {"updated_at": "2026-09-08T11:00:00Z", "products": []}, 1, self.now)
        self.assertIn("Dos o más ejecuciones consecutivas sin éxito", problems)
