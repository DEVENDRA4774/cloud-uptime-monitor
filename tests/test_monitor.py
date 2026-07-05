import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import monitor


class TestURLNormalization(unittest.TestCase):
    """Test URL normalization logic."""

    def test_auto_adds_https_scheme(self):
        result = monitor.normalize_url("example.com")
        self.assertEqual(result, "https://example.com")

    def test_strips_trailing_slash(self):
        result = monitor.normalize_url("https://example.com/")
        self.assertEqual(result, "https://example.com")

    def test_preserves_http_scheme(self):
        result = monitor.normalize_url("http://example.com")
        self.assertEqual(result, "http://example.com")

    def test_empty_string_returns_empty(self):
        result = monitor.normalize_url("")
        self.assertEqual(result, "")

    def test_whitespace_stripped(self):
        result = monitor.normalize_url("  https://example.com  ")
        self.assertEqual(result, "https://example.com")


class TestURLValidation(unittest.TestCase):
    """Test URL validation and SSRF protection."""

    def test_rejects_empty_url(self):
        with self.assertRaises(ValueError):
            monitor.validate_url("")

    def test_rejects_ftp_scheme(self):
        with self.assertRaises(ValueError):
            monitor.validate_url("ftp://example.com")

    def test_rejects_localhost(self):
        with self.assertRaises(ValueError):
            monitor.validate_url("http://localhost")

    def test_rejects_loopback_ip(self):
        with self.assertRaises(ValueError):
            monitor.validate_url("https://127.0.0.1")

    def test_rejects_private_ip_10(self):
        with self.assertRaises(ValueError):
            monitor.validate_url("https://10.0.0.1")

    def test_rejects_private_ip_192(self):
        with self.assertRaises(ValueError):
            monitor.validate_url("http://192.168.1.1")

    def test_accepts_valid_https_url(self):
        result = monitor.validate_url("https://example.com")
        self.assertEqual(result, "https://example.com")

    def test_accepts_url_without_scheme(self):
        result = monitor.validate_url("example.com")
        self.assertEqual(result, "https://example.com")


class TestCheckURL(unittest.TestCase):
    """Test HTTP health check logic."""

    def test_successful_check_returns_up(self):
        fake_resp = MagicMock()
        fake_resp.status_code = 200

        with patch.object(monitor.requests, "get", return_value=fake_resp):
            status_code, response_ms, is_up, _ = monitor.check_url("https://example.com")

        self.assertEqual(status_code, 200)
        self.assertTrue(is_up)

    def test_server_error_returns_down(self):
        fake_resp = MagicMock()
        fake_resp.status_code = 500

        with patch.object(monitor.requests, "get", return_value=fake_resp):
            status_code, response_ms, is_up, _ = monitor.check_url("https://example.com")

        self.assertEqual(status_code, 500)
        self.assertFalse(is_up)

    def test_retries_before_failure(self):
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

    def test_all_retries_exhausted_returns_down(self):
        with patch.object(monitor.requests, "get", side_effect=RuntimeError("timeout")):
            status_code, response_ms, is_up, _ = monitor.check_url("https://example.com")

        self.assertIsNone(status_code)
        self.assertFalse(is_up)


class TestDatabaseOperations(unittest.TestCase):
    """Test DB init, logging, and pruning with temp database."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_db = monitor.DB_FILE
        self._orig_dir = monitor.DATA_DIR
        monitor.DB_FILE = os.path.join(self.tmpdir, "test.db")
        monitor.DATA_DIR = self.tmpdir
        monitor.init_db()

    def tearDown(self):
        monitor.DB_FILE = self._orig_db
        monitor.DATA_DIR = self._orig_dir

    def test_init_db_creates_tables(self):
        conn = sqlite3.connect(monitor.DB_FILE)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        conn.close()
        table_names = [t[0] for t in tables]
        self.assertIn("uptime_logs", table_names)
        self.assertIn("monitors", table_names)

    def test_log_result_inserts_row(self):
        monitor.log_result("https://example.com", 200, 42.5, True)
        conn = sqlite3.connect(monitor.DB_FILE)
        count = conn.execute("SELECT COUNT(*) FROM uptime_logs").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_fetch_recent_logs_respects_limit(self):
        for i in range(10):
            monitor.log_result("https://example.com", 200, 10.0, True)
        logs = monitor.fetch_recent_logs(limit=5)
        self.assertEqual(len(logs), 5)

    def test_prune_old_logs_removes_stale_rows(self):
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

    def test_fetch_summary_returns_correct_structure(self):
        monitor.log_result("https://example.com", 200, 50.0, True)
        monitor.log_result("https://example.com", 500, 100.0, False)
        summary = monitor.fetch_summary()
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["total"], 2)
        self.assertEqual(summary[0]["up_count"], 1)
        self.assertEqual(summary[0]["uptime_pct"], 50.0)

    def test_add_and_get_active_urls(self):
        initial_count = len(monitor.get_active_urls())
        monitor.add_monitor("https://newsite.com")
        urls = monitor.get_active_urls()
        self.assertIn("https://newsite.com", urls)
        self.assertEqual(len(urls), initial_count + 1)

    def test_stop_monitor_deactivates(self):
        monitor.add_monitor("https://stopme.com")
        monitor.stop_monitor("https://stopme.com")
        urls = monitor.get_active_urls()
        self.assertNotIn("https://stopme.com", urls)

    def test_resume_monitor_reactivates(self):
        monitor.add_monitor("https://pauseme.com")
        monitor.stop_monitor("https://pauseme.com")
        monitor.resume_monitor("https://pauseme.com")
        urls = monitor.get_active_urls()
        self.assertIn("https://pauseme.com", urls)


class TestAPIEndpoints(unittest.TestCase):
    """Test Flask API routes."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_db = monitor.DB_FILE
        self._orig_dir = monitor.DATA_DIR
        monitor.DB_FILE = os.path.join(self.tmpdir, "test.db")
        monitor.DATA_DIR = self.tmpdir
        monitor.init_db()
        self.app = monitor.app.test_client()

    def tearDown(self):
        monitor.DB_FILE = self._orig_db
        monitor.DATA_DIR = self._orig_dir

    def test_health_endpoint_returns_ok(self):
        resp = self.app.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["version"], "2.0")

    def test_status_endpoint_returns_structure(self):
        resp = self.app.get("/api/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("latest", data)
        self.assertIn("summary", data)
        self.assertIn("config", data)
        self.assertIn("state", data)

    def test_add_url_rejects_empty(self):
        resp = self.app.post("/api/urls", json={"url": ""})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.get_json()["ok"])

    def test_add_url_accepts_valid(self):
        resp = self.app.post("/api/urls", json={"url": "https://httpbin.org"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["ok"])

    def test_logs_endpoint_returns_list(self):
        resp = self.app.get("/api/logs")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.get_json(), list)

    def test_logs_endpoint_respects_limit(self):
        resp = self.app.get("/api/logs?limit=5")
        self.assertEqual(resp.status_code, 200)


class TestDiscordAlerts(unittest.TestCase):
    """Test Discord webhook alert logic."""

    def test_handle_alerts_fires_down_event(self):
        monitor.currently_down.discard("https://test.com")
        with patch.object(monitor, "send_discord_alert") as mock_alert:
            monitor.handle_alerts("https://test.com", False, None, 0)
        mock_alert.assert_called_once_with("https://test.com", None, 0, "DOWN")
        self.assertIn("https://test.com", monitor.currently_down)

    def test_handle_alerts_fires_recovered_event(self):
        monitor.currently_down.add("https://test.com")
        with patch.object(monitor, "send_discord_alert") as mock_alert:
            monitor.handle_alerts("https://test.com", True, 200, 50.0)
        mock_alert.assert_called_once_with("https://test.com", 200, 50.0, "RECOVERED")
        self.assertNotIn("https://test.com", monitor.currently_down)

    def test_no_alert_if_already_down(self):
        monitor.currently_down.add("https://test.com")
        with patch.object(monitor, "send_discord_alert") as mock_alert:
            monitor.handle_alerts("https://test.com", False, None, 0)
        mock_alert.assert_not_called()


class TestSSLChecker(unittest.TestCase):
    """Test SSL certificate expiration checker."""

    @patch("ssl.create_default_context")
    @patch("socket.create_connection")
    def test_get_ssl_days_remaining_success(self, mock_conn, mock_ssl_ctx):
        mock_sock = MagicMock()
        mock_ssock = MagicMock()
        mock_conn.return_value.__enter__.return_value = mock_sock
        mock_ssl_ctx.return_value.wrap_socket.return_value.__enter__.return_value = mock_ssock
        
        from datetime import datetime, timedelta, timezone
        future_date = datetime.now(timezone.utc) + timedelta(days=5)
        expire_date_str = future_date.strftime("%b %d %H:%M:%S %Y GMT")
        
        mock_ssock.getpeercert.return_value = {
            'notAfter': expire_date_str
        }
        
        days = monitor.get_ssl_days_remaining("example.com")
        self.assertIsNotNone(days)
        self.assertIn(days, [4, 5])

    @patch("ssl.create_default_context")
    @patch("socket.create_connection")
    def test_get_ssl_days_remaining_failure_returns_none(self, mock_conn, mock_ssl_ctx):
        mock_conn.side_effect = RuntimeError("connection refused")
        days = monitor.get_ssl_days_remaining("example.com")
        self.assertIsNone(days)


class TestAWSAlerts(unittest.TestCase):
    """Test AWS SNS alert notification logic."""

    def setUp(self):
        self._orig_arn = monitor.AWS_SNS_TOPIC_ARN
        monitor.AWS_SNS_TOPIC_ARN = "arn:aws:sns:us-east-1:123456789012:MyTopic"

    def tearDown(self):
        monitor.AWS_SNS_TOPIC_ARN = self._orig_arn

    @patch("monitor.boto3.client")
    def test_send_aws_sns_alert_success(self, mock_boto_client):
        mock_sns = MagicMock()
        mock_boto_client.return_value = mock_sns
        
        success = monitor.send_aws_sns_alert("https://example.com", 200, 45.2, "RECOVERED")
        
        self.assertTrue(success)
        mock_sns.publish.assert_called_once()
        kwargs = mock_sns.publish.call_args[1]
        self.assertEqual(kwargs["TopicArn"], "arn:aws:sns:us-east-1:123456789012:MyTopic")
        self.assertIn("Site Online", kwargs["Subject"])
        self.assertIn("RECOVERED", kwargs["Message"])

    @patch("monitor.boto3.client")
    def test_send_aws_sns_alert_no_arn_skips(self, mock_boto_client):
        monitor.AWS_SNS_TOPIC_ARN = ""
        mock_sns = MagicMock()
        mock_boto_client.return_value = mock_sns
        
        success = monitor.send_aws_sns_alert("https://example.com", 200, 45.2, "RECOVERED")
        
        self.assertFalse(success)
        mock_sns.publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
