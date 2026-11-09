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

    def test_production_missing_secret_key_raises_runtime_error(self):
        """Production environment without FLASK_SECRET_KEY must fail startup safely with RuntimeError."""
        from app import resolve_flask_secret
        with patch.dict(os.environ, {"FLASK_SECRET_KEY": "", "SECRET_KEY": ""}, clear=False):
            with self.assertRaises(RuntimeError) as ctx:
                resolve_flask_secret(is_production=True)
            self.assertIn("Production requires an explicitly configured, secure FLASK_SECRET_KEY", str(ctx.exception))

    def test_production_placeholder_secret_key_raises_runtime_error(self):
        """Production environment with placeholder or weak secret key must raise RuntimeError."""
        from app import resolve_flask_secret
        for placeholder in ("your_flask_secret_key_here", "change-me", "dev", "123456", "short-key"):
            with patch.dict(os.environ, {"FLASK_SECRET_KEY": placeholder, "SECRET_KEY": ""}, clear=False):
                with self.assertRaises(RuntimeError):
                    resolve_flask_secret(is_production=True)

    def test_production_valid_secret_key_accepted(self):
        """Production environment with valid high-entropy secret key succeeds."""
        from app import resolve_flask_secret
        valid_key = "a" * 64
        with patch.dict(os.environ, {"FLASK_SECRET_KEY": valid_key, "SECRET_KEY": ""}, clear=False):
            resolved = resolve_flask_secret(is_production=True)
            self.assertEqual(resolved, valid_key)

    def test_development_ephemeral_secret_generation(self):
        """Development environment generates an ephemeral 32-byte secret key when unconfigured."""
        from app import resolve_flask_secret
        with patch.dict(os.environ, {"FLASK_SECRET_KEY": "", "SECRET_KEY": ""}, clear=False):
            key = resolve_flask_secret(is_production=False)
            self.assertEqual(len(key), 64)  # 32 bytes = 64 hex characters

    def test_development_configured_secret_accepted(self):
        """Development environment uses configured secret if valid."""
        from app import resolve_flask_secret
        valid_key = "d" * 64
        with patch.dict(os.environ, {"FLASK_SECRET_KEY": valid_key, "SECRET_KEY": ""}, clear=False):
            resolved = resolve_flask_secret(is_production=False)
            self.assertEqual(resolved, valid_key)

    def test_production_startup_blocks_when_secret_key_missing(self):
        """In production mode, missing FLASK_SECRET_KEY safely blocks application functionality with HTTP 500."""
        from app import app
        client = app.test_client()
        with patch("app._startup_error", "CRITICAL: Production requires an explicitly configured, secure FLASK_SECRET_KEY"):
            # HTML request
            res_html = client.get("/")
            self.assertEqual(res_html.status_code, 500)
            self.assertIn("Configuration Required", res_html.get_data(as_text=True))

            # API request
            res_api = client.get("/api/environment")
            self.assertEqual(res_api.status_code, 500)
            data = res_api.get_json()
            self.assertEqual(data.get("code"), "CONFIGURATION_REQUIRED")


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
        self.assertEqual(data.get("version"), "1.6.4")

    def test_api_health_endpoint(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data.get("status"), "healthy")
        self.assertEqual(data.get("version"), "1.6.4")


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


class TestSSRFHardening(unittest.TestCase):
    """Test SSRF rejection of CGNAT, metadata, private IPs, and DNS rebinding protections."""

    def test_cgnat_rejection(self):
        from scraper import is_safe_ip, validate_safe_url, ScraperError
        self.assertFalse(is_safe_ip("100.64.0.1"))
        self.assertFalse(is_safe_ip("100.127.255.254"))
        with self.assertRaises(ScraperError) as ctx:
            validate_safe_url("http://100.64.0.1/admin")
        self.assertIn("restricted for security", str(ctx.exception))

    def test_cloud_metadata_rejection(self):
        from scraper import is_safe_ip, validate_safe_url, ScraperError
        self.assertFalse(is_safe_ip("169.254.169.254"))
        self.assertFalse(is_safe_ip("100.100.100.200"))
        self.assertFalse(is_safe_ip("fd00:ec2::254"))
        with self.assertRaises(ScraperError):
            validate_safe_url("http://169.254.169.254/latest/meta-data/")

    def test_private_and_loopback_rejection(self):
        from scraper import is_safe_ip, validate_safe_url, ScraperError
        for ip in ("127.0.0.1", "10.0.0.1", "192.168.1.1", "172.16.0.1", "::1"):
            self.assertFalse(is_safe_ip(ip))
        with self.assertRaises(ScraperError):
            validate_safe_url("http://localhost:5000")
        with self.assertRaises(ScraperError):
            validate_safe_url("http://127.0.0.1:8080")

    def test_dns_rebinding_mixed_ip_rejection(self):
        """Simulates DNS resolving to a mixed safe and unsafe IP, ensuring immediate rejection."""
        import socket
        from scraper import resolve_and_validate_safe_url, ScraperError
        mock_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("100.64.0.1", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=mock_addrinfo):
            with self.assertRaises(ScraperError) as ctx:
                resolve_and_validate_safe_url("http://rebind-test.com")
            self.assertIn("restricted for security", str(ctx.exception))

    def test_response_size_limits_constant(self):
        from scraper import MAX_RESPONSE_BYTES, MAX_SUBREQUESTS
        self.assertEqual(MAX_RESPONSE_BYTES, 5 * 1024 * 1024)
        self.assertEqual(MAX_SUBREQUESTS, 50)

    def test_playwright_direct_unsafe_url_rejected(self):
        """Playwright browser invocation directly rejects SSRF/private destinations upfront."""
        from scraper import scrape_with_browser, ScraperError
        with self.assertRaises(ScraperError) as ctx:
            scrape_with_browser("http://127.0.0.1:8080/internal")
        self.assertIn("restricted for security", str(ctx.exception))

    def test_playwright_oversized_response_interception(self):
        """Mock Playwright run exceeding MAX_RESPONSE_BYTES raises ScraperError."""
        from unittest.mock import MagicMock
        from scraper import scrape_with_browser, ScraperError

        mock_page = MagicMock()
        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context

        mock_pw = MagicMock()
        mock_pw.chromium.launch.return_value = mock_browser

        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_pw

        # Simulate page.content() returning 6 MB
        oversized_content = "A" * (6 * 1024 * 1024)
        mock_page.content.return_value = oversized_content
        mock_page.url = "https://example.com/huge"

        with patch("playwright.sync_api.sync_playwright", return_value=mock_cm):
            with patch("scraper.validate_safe_url", return_value="https://example.com/huge"):
                with self.assertRaises(ScraperError) as ctx:
                    scrape_with_browser("https://example.com/huge")
                self.assertIn("exceeds maximum allowed size", str(ctx.exception))
                mock_context.close.assert_called_once()
                mock_browser.close.assert_called_once()

    def test_playwright_missing_content_length_oversized_response_interception(self):
        """Playwright route_handler intercepts oversized responses even when Content-Length is missing."""
        from unittest.mock import MagicMock
        from scraper import scrape_with_browser, ScraperError

        mock_page = MagicMock()
        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_pw = MagicMock()
        mock_pw.chromium.launch.return_value = mock_browser
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_pw

        # Capture the route_handler passed to page.route
        registered_handler = None
        def mock_route_fn(pattern, handler):
            nonlocal registered_handler
            registered_handler = handler
        mock_page.route.side_effect = mock_route_fn

        # When goto executes, simulate a route firing with missing Content-Length and 6MB body
        def mock_goto(url, **kwargs):
            if registered_handler:
                mock_route = MagicMock()
                mock_req = MagicMock()
                mock_req.resource_type = "document"
                mock_req.url = "https://example.com/stream"
                mock_route.request = mock_req

                mock_resp = MagicMock()
                mock_resp.headers = {}  # Missing Content-Length!
                mock_resp.body.return_value = b"X" * (6 * 1024 * 1024)
                mock_route.fetch.return_value = mock_resp

                registered_handler(mock_route)
                mock_route.abort.assert_called_with("aborted")

        mock_page.goto.side_effect = mock_goto
        mock_page.url = "https://example.com/stream"

        with patch("playwright.sync_api.sync_playwright", return_value=mock_cm):
            with patch("scraper.validate_safe_url", return_value="https://example.com/stream"):
                with self.assertRaises(ScraperError) as ctx:
                    scrape_with_browser("https://example.com/stream")
                self.assertIn("exceeds maximum allowed size", str(ctx.exception))
                mock_context.close.assert_called_once()
                mock_browser.close.assert_called_once()

    def test_playwright_inaccurate_content_length_oversized_response_interception(self):
        """Playwright route_handler inspects actual body bytes when Content-Length is underreported."""
        from unittest.mock import MagicMock
        from scraper import scrape_with_browser, ScraperError

        mock_page = MagicMock()
        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_pw = MagicMock()
        mock_pw.chromium.launch.return_value = mock_browser
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_pw

        registered_handler = None
        def mock_route_fn(pattern, handler):
            nonlocal registered_handler
            registered_handler = handler
        mock_page.route.side_effect = mock_route_fn

        def mock_goto(url, **kwargs):
            if registered_handler:
                mock_route = MagicMock()
                mock_req = MagicMock()
                mock_req.resource_type = "document"
                mock_req.url = "https://example.com/fake-cl"
                mock_route.request = mock_req

                mock_resp = MagicMock()
                mock_resp.headers = {"content-length": "100"}  # Inaccurate declared size!
                mock_resp.body.return_value = b"Y" * (6 * 1024 * 1024)  # Actual 6 MB
                mock_route.fetch.return_value = mock_resp

                registered_handler(mock_route)
                mock_route.abort.assert_called_with("aborted")

        mock_page.goto.side_effect = mock_goto
        mock_page.url = "https://example.com/fake-cl"

        with patch("playwright.sync_api.sync_playwright", return_value=mock_cm):
            with patch("scraper.validate_safe_url", return_value="https://example.com/fake-cl"):
                with self.assertRaises(ScraperError) as ctx:
                    scrape_with_browser("https://example.com/fake-cl")
                self.assertIn("exceeds maximum allowed size", str(ctx.exception))
                mock_context.close.assert_called_once()
                mock_browser.close.assert_called_once()

    def test_playwright_cleanup_on_browser_timeout(self):
        """Browser and context are reliably closed when Playwright navigation encounters timeout."""
        from unittest.mock import MagicMock
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from scraper import scrape_with_browser, ScraperError

        mock_page = MagicMock()
        mock_page.goto.side_effect = PlaywrightTimeoutError("Navigation timeout")
        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_pw = MagicMock()
        mock_pw.chromium.launch.return_value = mock_browser
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_pw

        with patch("playwright.sync_api.sync_playwright", return_value=mock_cm):
            with patch("scraper.validate_safe_url", return_value="https://example.com/timeout"):
                with self.assertRaises(ScraperError) as ctx:
                    scrape_with_browser("https://example.com/timeout")
                self.assertIn("timed out", str(ctx.exception))
                mock_context.close.assert_called_once()
                mock_browser.close.assert_called_once()


    def test_playwright_missing_executable_error_handling(self):
        """When Chromium binary is missing in serverless environments, error is cleanly sanitized."""
        from unittest.mock import MagicMock
        from scraper import scrape_with_browser, ScraperError

        mock_pw = MagicMock()
        mock_pw.chromium.launch.side_effect = Exception(
            "BrowserType.launch: Executable doesn't exist at /home/sbx_user1051/.cache/ms-playwright/chromium\n"
            "\u2554\u2550\u2550\u2550\n"
            "\u2551 Looks like Playwright was just installed or updated.\n"
            "\u255a\u2550\u2550\u2550"
        )
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_pw

        with patch("playwright.sync_api.sync_playwright", return_value=mock_cm):
            with patch("scraper.validate_safe_url", return_value="https://example.com/no-browser"):
                with self.assertRaises(ScraperError) as ctx:
                    scrape_with_browser("https://example.com/no-browser")
                err_msg = str(ctx.exception)
                self.assertIn("Headless browser engine (Chromium) is not installed", err_msg)
                self.assertNotIn("sbx_user", err_msg)
                self.assertNotIn("\u2554", err_msg)

    def test_scrape_article_preserves_static_content_when_browser_missing(self):
        """If static extraction yielded content and dynamic fallback fails due to missing browser, content is preserved."""
        from unittest.mock import MagicMock
        from scraper import scrape_article

        mock_pw = MagicMock()
        mock_pw.chromium.launch.side_effect = Exception("Executable doesn't exist at /home/sbx_user/.cache")
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_pw

        short_html = "<html><head><title>Test Article</title></head><body><p>Short paragraph extracted statically.</p></body></html>"

        with patch("playwright.sync_api.sync_playwright", return_value=mock_cm):
            with patch("scraper.validate_safe_url", return_value="https://example.com/short"):
                with patch("scraper.scrape_with_requests", return_value=short_html):
                    result = scrape_article("https://example.com/short")
                    self.assertIn("Short paragraph extracted statically.", result)
                    self.assertIn("Test Article", result)

    def test_scrape_article_clean_error_when_empty_and_browser_missing(self):
        """If static extraction yielded no content and dynamic fallback fails, a helpful hosted error is raised."""
        from unittest.mock import MagicMock
        from scraper import scrape_article, ScraperError

        mock_pw = MagicMock()
        mock_pw.chromium.launch.side_effect = Exception("Executable doesn't exist at /home/sbx_user/.cache")
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_pw

        empty_html = "<html><head><title>SPA</title></head><body><div id='app'></div></body></html>"

        with patch("playwright.sync_api.sync_playwright", return_value=mock_cm):
            with patch("scraper.validate_safe_url", return_value="https://example.com/spa"):
                with patch("scraper.scrape_with_requests", return_value=empty_html):
                    with self.assertRaises(ScraperError) as ctx:
                        scrape_article("https://example.com/spa")
                    err_msg = str(ctx.exception)
                    self.assertIn("This webpage requires client-side JavaScript execution", err_msg)
                    self.assertIn("hosted serverless environment", err_msg)
                    self.assertNotIn("sbx_user", err_msg)


class TestRateLimiting(unittest.TestCase):
    """Test built-in sliding-window rate limiting on sensitive routes."""

    def setUp(self):
        from app import app, rate_limiter
        app.config["TESTING"] = True
        self.client = app.test_client()
        self.limiter = rate_limiter
        self.limiter._events.clear()
        self.client.get("/")
        with self.client.session_transaction() as sess:
            self.csrf = sess.get("_csrf_token", "")

    def test_validate_key_rate_limit(self):
        for _ in range(20):
            res = self.client.post(
                "/api/validate-key",
                json={"provider": "groq", "groq_api_key": "gsk_test"},
                headers={"X-CSRF-Token": self.csrf}
            )
            self.assertNotEqual(res.status_code, 429)

        # 21st request triggers rate limit 429
        res = self.client.post(
            "/api/validate-key",
            json={"provider": "groq", "groq_api_key": "gsk_test"},
            headers={"X-CSRF-Token": self.csrf}
        )
        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.get_json().get("code"), "RATE_LIMIT_EXCEEDED")

    def test_redis_rate_limiter_exceeded(self):
        """When Redis rate limit counter exceeds threshold, returns False."""
        from unittest.mock import MagicMock
        from app import is_rate_limited_redis
        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_pipe.execute.return_value = (25, True)
        mock_redis.pipeline.return_value = mock_pipe

        allowed = is_rate_limited_redis(mock_redis, "test_key", max_requests=20, window_seconds=60)
        self.assertFalse(allowed)

    def test_redis_rate_limiter_error_falls_back_to_none(self):
        """When Redis throws an exception, returns None to trigger graceful fallback to in-memory limiter."""
        from unittest.mock import MagicMock
        from redis.exceptions import RedisError
        from app import is_rate_limited_redis
        mock_redis = MagicMock()
        mock_redis.pipeline.side_effect = RedisError("Connection lost")

        allowed = is_rate_limited_redis(mock_redis, "test_key", max_requests=20, window_seconds=60)
        self.assertIsNone(allowed)


class TestFrontendDOMInjection(unittest.TestCase):
    """Test templates against XSS and unsafe DOM interpolation."""

    def test_index_html_notion_selector_uses_safe_dom_construction(self):
        with open("templates/index.html", "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("select.innerHTML = json.pages.map", content)
        self.assertIn('document.createElement("option")', content)
        self.assertIn("opt.textContent =", content)

    def test_index_html_batch_url_input_does_not_interpolate_raw_val(self):
        with open("templates/index.html", "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn('value="${val}"', content)
        self.assertIn('inputEl.value = val || ""', content)


class TestNotionIntegration(unittest.TestCase):
    """Test Notion credential persistence contract, hosted vs local separation, and confidentiality."""

    def test_save_notion_key_returns_tuple_contract(self):
        """save_notion_key_to_env returns a (bool, str) tuple in all execution paths."""
        from notion_service import save_notion_key_to_env
        res_empty = save_notion_key_to_env("")
        self.assertIsInstance(res_empty, tuple)
        self.assertFalse(res_empty[0])
        self.assertIn("Invalid", res_empty[1])

        res_short = save_notion_key_to_env("short")
        self.assertIsInstance(res_short, tuple)
        self.assertFalse(res_short[0])
        self.assertIn("too short", res_short[1])

    def test_hosted_notion_storage_does_not_leak_to_session_or_filesystem(self):
        """In hosted mode without Redis vault, Notion token is rejected and never enters Flask session or .env."""
        from notion_service import save_notion_key_to_env, get_notion_key
        dummy_session = {}
        fake_token = "secret_" + "a" * 32
        success, msg = save_notion_key_to_env(fake_token, session_dict=dummy_session, is_hosted=True)
        self.assertFalse(success)
        self.assertIn("requires the secure encrypted Redis vault", msg)
        self.assertNotIn("_notion_token", dummy_session)
        # get_notion_key must ignore any session_dict keys
        with patch.dict(os.environ, {"NOTION_API_KEY": "", "NOTION_TOKEN": ""}, clear=False):
            retrieved = get_notion_key(session_dict={"_notion_token": fake_token, "notion_api_key": fake_token})
            self.assertIsNone(retrieved)

    def test_local_notion_storage_persists_to_env_safely(self):
        """In local mode, Notion key writes to specified .env file."""
        import tempfile
        from notion_service import save_notion_key_to_env, clear_saved_notion_key
        with tempfile.NamedTemporaryFile("w+", delete=False) as tf:
            tf.write("FLASK_ENV=development\n")
            temp_path = tf.name

        try:
            fake_token = "secret_" + "b" * 32
            success, msg = save_notion_key_to_env(fake_token, env_file_path=temp_path, is_hosted=False)
            self.assertTrue(success)
            with open(temp_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn(f"NOTION_API_KEY={fake_token}", content)

            # Clear key
            clear_saved_notion_key(env_file_path=temp_path)
            with open(temp_path, "r", encoding="utf-8") as f:
                cleared_content = f.read()
            self.assertNotIn("NOTION_API_KEY=", cleared_content)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_notion_endpoints_hosted_vault_isolation(self):
        """In hosted mode, /api/notion/connect saves to encrypted vault when configured; never exposes raw tokens."""
        from app import app
        client = app.test_client()
        client.get("/")
        with client.session_transaction() as sess:
            csrf = sess.get("_csrf_token", "")

        fake_token = "secret_" + "c" * 32
        mock_vault = MagicMock()
        mock_vault.save.return_value = True

        with patch("app.validate_notion_token", return_value=(True, "")):
            with patch("app.get_accessible_pages", return_value=[{"id": "p1", "title": "Dashboard", "icon": "📄"}]):
                with patch("app.is_serverless_env", return_value=True):
                    # Without vault: returns 400 VAULT_REQUIRED
                    with patch("app._vault", None):
                        res = client.post(
                            "/api/notion/connect",
                            json={"token": fake_token},
                            headers={"X-CSRF-Token": csrf}
                        )
                        self.assertEqual(res.status_code, 400)
                        self.assertEqual(res.get_json().get("code"), "VAULT_REQUIRED")

                    # With vault: returns 200 and stores in vault
                    with patch("app._vault", mock_vault):
                        res2 = client.post(
                            "/api/notion/connect",
                            json={"token": fake_token},
                            headers={"X-CSRF-Token": csrf}
                        )
                        self.assertEqual(res2.status_code, 200)
                        data = res2.get_json()
                        self.assertTrue(data.get("success"))
                        self.assertNotIn(fake_token, str(data))
                        mock_vault.save.assert_called()

                        # Verify cookie session did not receive raw notion token
                        with client.session_transaction() as sess2:
                            self.assertNotIn("_notion_token", sess2)
                            self.assertNotIn("notion_api_key", sess2)


if __name__ == "__main__":
    unittest.main()
