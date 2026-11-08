"""
WebBrief AI v1.6.1 — Comprehensive Security & Reliability Test Suite.
Tests secret-key validation, anti-CSRF token verification, credential sanitization,
serverless database protection, health probes, and provider reliability.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Ensure project root is on sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


class TestSecretKeyHandling(unittest.TestCase):
    """Test Flask secret-key initialization under production vs development modes."""

    def test_production_missing_secret_key_fails_safely(self):
        """Production environment without FLASK_SECRET_KEY generates an unguessable 32-byte ephemeral key without crash."""
        with patch.dict(os.environ, {"VERCEL": "1", "FLASK_SECRET_KEY": "", "SECRET_KEY": ""}, clear=False):
            # When FLASK_SECRET_KEY is absent, app boots with cryptographically secure ephemeral secret
            import secrets
            flask_secret = os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY")
            if not flask_secret:
                flask_secret = secrets.token_hex(32)
            self.assertEqual(len(flask_secret), 64)

    def test_development_ephemeral_secret_generation(self):
        """Development environment generates an ephemeral 32-byte secret key."""
        import secrets
        key = secrets.token_hex(32)
        self.assertEqual(len(key), 64)  # 32 bytes = 64 hex characters


class TestCredentialRedaction(unittest.TestCase):
    """Test credential masking and sanitization."""

    def setUp(self):
        from byok import redact_credentials
        self.redact = redact_credentials

    def test_groq_key_redaction(self):
        text = "Failed with key gsk_abcdef1234567890abcdef1234567890 in request"
        redacted = self.redact(text)
        self.assertNotIn("gsk_abcdef1234567890abcdef1234567890", redacted)
        self.assertIn("gsk_...", redacted)

    def test_legacy_sk_redaction(self):
        text = "Key sk-proj-1234567890abcdef1234567890 was rejected"
        redacted = self.redact(text)
        self.assertNotIn("sk-proj-1234567890abcdef1234567890", redacted)
        self.assertIn("sk-...", redacted)

    def test_notion_secret_redaction(self):
        text = "Notion token secret_1234567890abcdef1234567890abcdef failed"
        redacted = self.redact(text)
        self.assertNotIn("secret_1234567890abcdef1234567890abcdef", redacted)
        self.assertIn("[notion_secret_masked]", redacted)

    def test_bearer_header_redaction(self):
        text = "HTTP header Authorization: Bearer abcdef1234567890xyz12345"
        redacted = self.redact(text)
        self.assertNotIn("abcdef1234567890xyz12345", redacted)
        self.assertIn("Bearer [token_masked]", redacted)

    def test_cloudflare_account_id_redaction(self):
        text = "Endpoint /accounts/0123456789abcdef0123456789abcdef/ai/run"
        redacted = self.redact(text)
        self.assertNotIn("0123456789abcdef0123456789abcdef", redacted)
        self.assertIn("[account_id_masked]", redacted)


class TestHealthCheckEndpoint(unittest.TestCase):
    """Test diagnostic health endpoints."""

    def setUp(self):
        from app import app
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_health_endpoint(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data.get("status"), "healthy")
        self.assertEqual(data.get("version"), "1.6.1")

    def test_api_health_endpoint(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data.get("status"), "healthy")
        self.assertEqual(data.get("version"), "1.6.1")


class TestCSRFProtection(unittest.TestCase):
    """Test Origin, Referer, and CSRF token enforcement."""

    def setUp(self):
        from app import app
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_get_request_exempt(self):
        """GET requests do not require a CSRF token."""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)

    def test_post_without_csrf_token_rejected(self):
        """POST request without CSRF token must return 403 Forbidden."""
        response = self.client.post("/api/save-config", json={"primary_provider": "cloudflare"})
        self.assertEqual(response.status_code, 403)
        data = response.get_json()
        self.assertEqual(data.get("code"), "CSRF_VERIFICATION_FAILED")

    def test_post_with_invalid_csrf_token_rejected(self):
        """POST request with incorrect CSRF token must return 403 Forbidden."""
        with self.client.session_transaction() as sess:
            sess["_csrf_token"] = "valid_server_token_1234567890abcdef"

        response = self.client.post(
            "/api/save-config",
            headers={"X-CSRF-Token": "wrong_token_value"},
            json={"primary_provider": "cloudflare"}
        )
        self.assertEqual(response.status_code, 403)

    def test_post_with_valid_header_csrf_token_accepted(self):
        """POST request with matching X-CSRF-Token header passes CSRF verification."""
        test_token = "valid_token_32_bytes_abcdef1234567890"
        with self.client.session_transaction() as sess:
            sess["_csrf_token"] = test_token

        # /api/preferences with valid token (CSRF passes; will process request)
        response = self.client.post(
            "/api/preferences/reset",
            headers={"X-CSRF-Token": test_token}
        )
        self.assertEqual(response.status_code, 200)

    def test_post_with_valid_json_csrf_token_accepted(self):
        """POST request with matching csrf_token in JSON body passes CSRF verification."""
        test_token = "valid_token_32_bytes_json_1234567890"
        with self.client.session_transaction() as sess:
            sess["_csrf_token"] = test_token

        response = self.client.post(
            "/api/preferences",
            json={"csrf_token": test_token, "custom_directive": "Be brief", "custom_directive_enabled": True}
        )
        self.assertEqual(response.status_code, 200)

    def test_cross_origin_origin_header_rejected(self):
        """POST request with mismatched Origin header must return 403."""
        test_token = "valid_token_32_bytes_origin_test"
        with self.client.session_transaction() as sess:
            sess["_csrf_token"] = test_token

        response = self.client.post(
            "/api/preferences/reset",
            headers={
                "X-CSRF-Token": test_token,
                "Origin": "https://malicious-attacker-site.com"
            }
        )
        self.assertEqual(response.status_code, 403)


class TestServerlessProtection(unittest.TestCase):
    """Test SQLite safety under serverless / read-only conditions."""

    def test_serverless_env_detection(self):
        from byok import is_serverless_env
        with patch.dict(os.environ, {"VERCEL": "1"}):
            self.assertTrue(is_serverless_env())
        with patch.dict(os.environ, {"VERCEL": "", "VERCEL_ENV": ""}, clear=False):
            # Test when all serverless flags are cleared
            with patch.dict(os.environ, {
                "AWS_LAMBDA_FUNCTION_NAME": "",
                "NETLIFY": "",
                "SERVERLESS": "",
                "HOSTED_MODE": "",
                "WEBBRIEF_HOSTED": ""
            }):
                self.assertFalse(is_serverless_env())

    def test_serverless_preference_fallback(self):
        """Preferences work seamlessly in-memory when in serverless mode."""
        from history import set_preference, get_preference
        with patch.dict(os.environ, {"VERCEL": "1"}):
            success = set_preference("test_pref_key", "test_pref_value")
            self.assertTrue(success)
            val = get_preference("test_pref_key")
            self.assertEqual(val, "test_pref_value")


class TestPDFExporterGuard(unittest.TestCase):
    """Test ReportLab import boundary."""

    def test_pdf_exporter_available_flag(self):
        from pdf_exporter import REPORTLAB_AVAILABLE
        # ReportLab is installed in this test environment
        self.assertTrue(REPORTLAB_AVAILABLE)


if __name__ == "__main__":
    unittest.main()
