"""
Phase 3 Production Hardening, Security, Data Recovery & Observability Tests.
Validates:
- SEC-01 / SEC-02: CSP headers, secure attributes, nosniff, frame-ancestors.
- SEC-03: safe_url filter neutralizing javascript: and data: pseudo-schemes.
- SEC-05: Redaction of Redis passwords, Fernet keys, and vault tokens.
- DB-01 / DB-02: Safe SQLite backup (VACUUM INTO / WAL checkpoint) and durability.
- AI-01 / AI-03: Provider retry behavior (retry on transient 503/timeout, fail fast on auth/quota).
- OBS-01: Request ID and Response Time tracing headers.
- OBS-02: Metrics endpoint and counter tracking.
"""

import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from google.genai import errors

from app import app, increment_metric
from byok import redact_credentials
from history import backup_database, init_db, add_history_entry, get_history
from services.providers.gemini import GeminiProvider
from services.providers.exceptions import (
    ProviderError,
    ProviderAuthError,
    ProviderQuotaError,
)


class TestPhase3Hardening(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.client = self.app.test_client()

    def test_sec02_csp_headers_and_security_attributes(self):
        """SEC-02: Verify CSP contains defense-in-depth directives without breaking SPA."""
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)

        csp = resp.headers.get("Content-Security-Policy", "")
        self.assertIn("object-src 'none'", csp)
        self.assertIn("base-uri 'self'", csp)
        self.assertIn("form-action 'self'", csp)
        self.assertIn("frame-ancestors 'self'", csp)
        self.assertIn("connect-src 'self' blob:", csp)
        self.assertIn("img-src 'self' data: https: blob:", csp)

        self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(resp.headers.get("X-Frame-Options"), "SAMEORIGIN")

    def test_sec03_safe_url_filter_blocks_javascript_schemes(self):
        """SEC-03: Verify safe_url filter neutralizes javascript: and data: URIs in Jinja."""
        safe_url_filter = self.app.jinja_env.filters["safe_url"]

        self.assertEqual(safe_url_filter("javascript:alert(1)"), "#")
        self.assertEqual(safe_url_filter("JAVASCRIPT:alert('xss')"), "#")
        self.assertEqual(safe_url_filter("javascript:void(0)"), "#")
        self.assertEqual(safe_url_filter("data:text/html,<script>alert(1)</script>"), "#")
        self.assertEqual(safe_url_filter("vbscript:msgbox(1)"), "#")
        self.assertEqual(safe_url_filter(""), "#")
        self.assertEqual(safe_url_filter(None), "#")

        # Legitimate URLs must remain intact
        self.assertEqual(safe_url_filter("https://example.com/path?arg=1"), "https://example.com/path?arg=1")
        self.assertEqual(safe_url_filter("http://localhost:5000/test"), "http://localhost:5000/test")
        self.assertEqual(safe_url_filter("mailto:test@example.com"), "mailto:test@example.com")

    def test_sec05_extended_credential_redaction(self):
        """SEC-05: Verify credentials redaction covers Redis URLs, Fernet keys, and tokens."""
        # Redis connection URL with password
        raw_redis_url = "Connecting to redis://:super_secret_redis_pass@redis.example.com:6379/0 failed"
        redacted_redis = redact_credentials(raw_redis_url)
        self.assertNotIn("super_secret_redis_pass", redacted_redis)
        self.assertIn("redis://[password_masked]@", redacted_redis)

        # Fernet encryption key (44 chars ending in =)
        fernet_key = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOP1="
        raw_msg = f"Failed to decrypt record with key {fernet_key}"
        redacted_fernet = redact_credentials(raw_msg)
        self.assertNotIn(fernet_key, redacted_fernet)
        self.assertIn("[fernet_key_masked]", redacted_fernet)

        # Vault session token
        raw_token = "Active session wb_vault_9876543210fedcba12345678 expired"
        redacted_token = redact_credentials(raw_token)
        self.assertNotIn("9876543210fedcba12345678", redacted_token)
        self.assertIn("wb_vault_[masked]", redacted_token)

    def test_db01_sqlite_safe_backup_vacuum_into(self):
        """DB-01: Verify safe SQLite backup via VACUUM INTO / WAL checkpoint."""
        temp_dir = tempfile.mkdtemp()
        src_db = os.path.join(temp_dir, "test_source.db")
        backup_db = os.path.join(temp_dir, "test_backup.db")

        try:
            init_db(src_db)
            add_history_entry(
                title="Backup Article 1",
                url="https://example.com/backup1",
                mode="concise",
                mode_name="Concise",
                summary={"overview": "Testing backup durability"},
                db_path=src_db,
            )
            add_history_entry(
                title="Backup Article 2",
                url="https://example.com/backup2",
                mode="detailed",
                mode_name="Detailed",
                summary={"overview": "Testing backup durability 2"},
                db_path=src_db,
            )

            # Perform safe online backup
            success = backup_database(destination_path=backup_db, db_path=src_db)
            self.assertTrue(success)
            self.assertTrue(os.path.exists(backup_db))
            self.assertGreater(os.path.getsize(backup_db), 0)

            # Validate destination database can be read and verified
            b_conn = sqlite3.connect(backup_db)
            integrity = b_conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")

            rows = b_conn.execute("SELECT title, url FROM summary_history ORDER BY created_at ASC").fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0][0], "Backup Article 1")
            self.assertEqual(rows[1][0], "Backup Article 2")
            b_conn.close()

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @patch("services.providers.gemini.time.sleep", return_value=None)
    @patch("services.providers.gemini.genai.Client")
    def test_ai03_gemini_retry_on_transient_503(self, mock_client_cls, mock_sleep):
        """AI-03: Verify GeminiProvider retries transient 503 errors and succeeds."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        err_503 = errors.APIError(503, {"error": {"message": "Service Unavailable"}}, None)

        mock_resp_200 = MagicMock()
        mock_resp_200.text = "Retry succeeded!"

        mock_client.models.generate_content.side_effect = [err_503, mock_resp_200]

        provider = GeminiProvider(api_key="AIzaSyTestMockKeyForRetryTesting12345")
        res = provider.generate_completion(
            messages=[{"role": "user", "content": "Test prompt"}],
            model="gemini-3.8-flash",
        )
        self.assertEqual(res, "Retry succeeded!")
        self.assertEqual(mock_sleep.call_count, 1)

    @patch("services.providers.gemini.time.sleep", return_value=None)
    @patch("services.providers.gemini.genai.Client")
    def test_ai03_gemini_no_retry_on_401_or_quota(self, mock_client_cls, mock_sleep):
        """AI-03: Verify GeminiProvider fails immediately without retry on 401/403/Quota."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        err_401 = errors.APIError(401, {"error": {"message": "API_KEY_INVALID"}}, None)
        mock_client.models.generate_content.side_effect = err_401

        provider = GeminiProvider(api_key="AIzaSyTestMockKeyForRetryTesting12345")
        with self.assertRaises(ProviderAuthError):
            provider.generate_completion(
                messages=[{"role": "user", "content": "Test prompt"}],
                model="gemini-3.8-flash",
            )
        # Must not retry auth errors
        self.assertEqual(mock_client.models.generate_content.call_count, 1)
        self.assertEqual(mock_sleep.call_count, 0)

    def test_obs01_request_id_and_response_time_headers(self):
        """OBS-01: Verify X-Request-ID and X-Response-Time headers in responses."""
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)

        req_id = resp.headers.get("X-Request-ID")
        self.assertIsNotNone(req_id)
        self.assertTrue(len(req_id) > 0)

        resp_time = resp.headers.get("X-Response-Time")
        self.assertIsNotNone(resp_time)
        self.assertTrue(resp_time.endswith("ms"))

        # Verify client-supplied request ID is preserved
        custom_id = "wb-client-req-999"
        resp2 = self.client.get("/health", headers={"X-Request-ID": custom_id})
        self.assertEqual(resp2.headers.get("X-Request-ID"), custom_id)

    def test_obs02_metrics_endpoint(self):
        """OBS-02: Verify /api/metrics returns operational telemetry."""
        initial_resp = self.client.get("/api/metrics")
        self.assertEqual(initial_resp.status_code, 200)
        data = initial_resp.get_json()

        self.assertEqual(data.get("status"), "healthy")
        self.assertNotIn("version", data)
        self.assertIn("metrics", data)
        metrics = data["metrics"]
        self.assertIn("summaries_success", metrics)
        self.assertIn("summaries_failed", metrics)
        self.assertIn("scrape_failures", metrics)

        initial_count = metrics["summaries_success"]
        increment_metric("summaries_success", 5)

        subsequent_resp = self.client.get("/api/metrics")
        subsequent_data = subsequent_resp.get_json()
        self.assertEqual(subsequent_data["metrics"]["summaries_success"], initial_count + 5)


if __name__ == "__main__":
    unittest.main()
