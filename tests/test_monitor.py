import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import monitor


class MonitorBehaviorTests(unittest.TestCase):
    def test_validate_url_rejects_internal_targets(self):
        for value in ["http://localhost", "https://127.0.0.1", "https://10.0.0.1", "ftp://example.com"]:
            with self.assertRaises(ValueError):
                monitor.validate_url(value)

    def test_check_url_retries_before_failure(self):
        class FakeResponse:
            status_code = 200

        responses = iter([RuntimeError("boom"), FakeResponse()])

        def fake_get(url, timeout):
            result = next(responses)
            if isinstance(result, Exception):
                raise result
            return result

        with patch.object(monitor.requests, "get", side_effect=fake_get):
            status_code, response_ms, is_up, cert_days = monitor.check_url("https://example.com")

        self.assertEqual(status_code, 200)
        self.assertTrue(is_up)
        self.assertEqual(cert_days, None)

    def test_prune_old_logs_removes_stale_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            monitor.DB_FILE = os.path.join(tmpdir, "test.db")
            monitor.DATA_DIR = tmpdir
            monitor.init_db()

            conn = sqlite3.connect(monitor.DB_FILE)
            conn.execute(
                "INSERT INTO uptime_logs(url, timestamp, status_code, response_ms, is_up) VALUES (?, ?, ?, ?, ?)",
                ("https://example.com", "2020-01-01 00:00:00", 200, 10.0, 1),
            )
            conn.execute(
                "INSERT INTO uptime_logs(url, timestamp, status_code, response_ms, is_up) VALUES (?, ?, ?, ?, ?)",
                ("https://example.com", "2099-01-01 00:00:00", 200, 10.0, 1),
            )
            conn.commit()
            conn.close()

            monitor.LOG_RETENTION_DAYS = 30
            monitor.prune_old_logs()

            conn = sqlite3.connect(monitor.DB_FILE)
            rows = conn.execute("SELECT COUNT(*) FROM uptime_logs").fetchone()[0]
            conn.close()
            self.assertEqual(rows, 1)


if __name__ == "__main__":
    unittest.main()
