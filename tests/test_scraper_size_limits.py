import gzip
import io
import os
import unittest
from unittest.mock import MagicMock, patch

from app import app
from scraper import (
    MAX_RESPONSE_BYTES,
    MAX_BROWSER_NETWORK_BYTES,
    MAX_RENDERED_HTML_BYTES,
    MAX_EXTRACTED_CONTENT_BYTES,
    ScraperError,
    scrape_article,
    scrape_with_requests,
    scrape_with_browser,
)
from summarizer import SummarizerError, summarize_text, MAX_SUMMARIZER_CHUNKS


class TestScraperSizeLimits(unittest.TestCase):
    """
    Comprehensive verification of the 10 MiB scraper-side size limits,
    streaming chunk enforcement, decompression bomb protection, Playwright accounting,
    DOM/extracted limits, and Flask 2 MiB incoming request constraint.
    """

    def setUp(self):
        self.ten_mib = 10 * 1024 * 1024

    def test_default_constants(self):
        """Verify all scraper limits default to 10 MiB independently."""
        self.assertEqual(MAX_RESPONSE_BYTES, 10 * 1024 * 1024)
        self.assertEqual(MAX_BROWSER_NETWORK_BYTES, 10 * 1024 * 1024)
        self.assertEqual(MAX_RENDERED_HTML_BYTES, 10 * 1024 * 1024)
        self.assertEqual(MAX_EXTRACTED_CONTENT_BYTES, 10 * 1024 * 1024)

    # -------------------------------------------------------------------------
    # A. Static HTTP Fetching Tests
    # -------------------------------------------------------------------------

    @patch("requests.Session")
    @patch("scraper.resolve_and_validate_safe_url")
    def test_static_response_under_10_mib(self, mock_validate, mock_session_cls):
        """Static response under 10 MiB (e.g. 1 MiB) downloads successfully."""
        mock_validate.return_value = ("https://example.com/page", "93.184.216.34")
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        mock_resp = MagicMock()
        mock_resp.is_redirect = False
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Length": str(1024 * 1024)}
        mock_resp.encoding = "utf-8"
        chunk_data = b"X" * (1024 * 1024)
        mock_resp.iter_content.return_value = [chunk_data]
        mock_session.get.return_value = mock_resp

        result = scrape_with_requests("https://example.com/page")
        self.assertEqual(len(result.encode("utf-8")), 1024 * 1024)

    @patch("requests.Session")
    @patch("scraper.resolve_and_validate_safe_url")
    def test_static_response_exact_10_mib(self, mock_validate, mock_session_cls):
        """Static response exactly at 10 MiB (10,485,760 bytes) downloads successfully."""
        mock_validate.return_value = ("https://example.com/exact", "93.184.216.34")
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        mock_resp = MagicMock()
        mock_resp.is_redirect = False
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Length": str(self.ten_mib)}
        mock_resp.encoding = "utf-8"
        # 160 chunks of 64 KiB = 10,485,760 bytes
        chunk = b"Z" * 65536
        mock_resp.iter_content.return_value = [chunk] * 160
        mock_session.get.return_value = mock_resp

        result = scrape_with_requests("https://example.com/exact")
        self.assertEqual(len(result.encode("utf-8")), self.ten_mib)

    @patch("requests.Session")
    @patch("scraper.resolve_and_validate_safe_url")
    def test_static_response_above_10_mib_content_length_preflight(self, mock_validate, mock_session_cls):
        """Static response declaring Content-Length > 10 MiB aborts before reading stream."""
        mock_validate.return_value = ("https://example.com/large", "93.184.216.34")
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        mock_resp = MagicMock()
        mock_resp.is_redirect = False
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Length": str(self.ten_mib + 1)}
        mock_session.get.return_value = mock_resp

        with self.assertRaises(ScraperError) as ctx:
            scrape_with_requests("https://example.com/large")
        self.assertIn("exceeds maximum allowed size", str(ctx.exception))
        # Ensure iter_content was never consumed
        mock_resp.iter_content.assert_not_called()

    @patch("requests.Session")
    @patch("scraper.resolve_and_validate_safe_url")
    def test_static_response_missing_content_length_streaming_abort(self, mock_validate, mock_session_cls):
        """When Content-Length is missing, streaming loop stops immediately at > 10 MiB."""
        mock_validate.return_value = ("https://example.com/chunked", "93.184.216.34")
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        mock_resp = MagicMock()
        mock_resp.is_redirect = False
        mock_resp.status_code = 200
        mock_resp.headers = {}  # Chunked transfer encoding / missing Content-Length

        # Yield 170 chunks of 64 KiB = 11,141,120 bytes (> 10 MiB)
        chunks = [b"A" * 65536 for _ in range(170)]
        mock_resp.iter_content.return_value = iter(chunks)
        mock_session.get.return_value = mock_resp

        with self.assertRaises(ScraperError) as ctx:
            scrape_with_requests("https://example.com/chunked")
        self.assertIn("exceeds maximum allowed size", str(ctx.exception))
        mock_resp.close.assert_called()

    @patch("requests.Session")
    @patch("scraper.resolve_and_validate_safe_url")
    def test_static_response_underreported_content_length(self, mock_validate, mock_session_cls):
        """When Content-Length is underreported (e.g. 500 bytes but sends 11 MiB), stream aborts at 10 MiB."""
        mock_validate.return_value = ("https://example.com/trick", "93.184.216.34")
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        mock_resp = MagicMock()
        mock_resp.is_redirect = False
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Length": "500"}  # Deliberately deceptive header

        chunks = [b"B" * 65536 for _ in range(170)]
        mock_resp.iter_content.return_value = iter(chunks)
        mock_session.get.return_value = mock_resp

        with self.assertRaises(ScraperError) as ctx:
            scrape_with_requests("https://example.com/trick")
        self.assertIn("exceeds maximum allowed size", str(ctx.exception))
        mock_resp.close.assert_called()

    @patch("requests.Session")
    @patch("scraper.resolve_and_validate_safe_url")
    def test_static_compressed_response_decompression_limit(self, mock_validate, mock_session_cls):
        """Compressed stream whose decompressed payload exceeds 10 MiB is halted by iter_content."""
        mock_validate.return_value = ("https://example.com/gzip-bomb", "93.184.216.34")
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        mock_resp = MagicMock()
        mock_resp.is_redirect = False
        mock_resp.status_code = 200
        # Declares 50 KB compressed header
        mock_resp.headers = {"Content-Length": "51200", "Content-Encoding": "gzip"}

        # iter_content yields decompressed data in requests; simulate decompression yielding > 10 MiB
        decompressed_chunks = [b"C" * 65536 for _ in range(170)]
        mock_resp.iter_content.return_value = iter(decompressed_chunks)
        mock_session.get.return_value = mock_resp

        with self.assertRaises(ScraperError) as ctx:
            scrape_with_requests("https://example.com/gzip-bomb")
        self.assertIn("exceeds maximum allowed size", str(ctx.exception))
        mock_resp.close.assert_called()

    # -------------------------------------------------------------------------
    # B. Playwright Cumulative Network Response Tests
    # -------------------------------------------------------------------------

    def test_playwright_cumulative_network_under_limit(self):
        """Playwright subrequests summing to under 10 MiB are accepted."""
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
                for i in range(3):
                    mock_route = MagicMock()
                    mock_req = MagicMock()
                    mock_req.resource_type = "document" if i == 0 else "script"
                    mock_req.url = f"https://example.com/asset-{i}"
                    mock_route.request = mock_req

                    mock_resp = MagicMock()
                    mock_resp.headers = {"content-length": "100000"}
                    mock_resp.body.return_value = b"M" * 100000
                    mock_route.fetch.return_value = mock_resp

                    registered_handler(mock_route)
                    mock_route.fulfill.assert_called_once()

        mock_page.goto.side_effect = mock_goto
        mock_page.url = "https://example.com/app"
        mock_page.content.return_value = "<html><body>Hello Playwright</body></html>"

        with patch("playwright.sync_api.sync_playwright", return_value=mock_cm):
            with patch("scraper.validate_safe_url", return_value="https://example.com/app"):
                html = scrape_with_browser("https://example.com/app")
                self.assertIn("Hello Playwright", html)
                mock_context.close.assert_called_once()
                mock_browser.close.assert_called_once()

    def test_playwright_cumulative_network_exceeding_10_mib(self):
        """Playwright multiple subrequests cumulatively exceeding 10 MiB abort the route and raise ScraperError."""
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
                # Subrequest 1: 6 MiB (OK)
                r1 = MagicMock()
                r1.request.resource_type = "document"
                r1.request.url = "https://example.com/main"
                resp1 = MagicMock()
                resp1.headers = {"content-length": str(6 * 1024 * 1024)}
                resp1.body.return_value = b"D" * (6 * 1024 * 1024)
                r1.fetch.return_value = resp1
                registered_handler(r1)
                r1.fulfill.assert_called_once()

                # Subrequest 2: 5 MiB (Cumulative = 11 MiB > 10 MiB -> ABORTED)
                r2 = MagicMock()
                r2.request.resource_type = "script"
                r2.request.url = "https://example.com/app.js"
                resp2 = MagicMock()
                resp2.headers = {"content-length": str(5 * 1024 * 1024)}
                resp2.body.return_value = b"E" * (5 * 1024 * 1024)
                r2.fetch.return_value = resp2
                registered_handler(r2)
                r2.abort.assert_called_with("aborted")

        mock_page.goto.side_effect = mock_goto
        mock_page.url = "https://example.com/app"
        mock_page.content.return_value = "<html><body>Oversized App</body></html>"

        with patch("playwright.sync_api.sync_playwright", return_value=mock_cm):
            with patch("scraper.validate_safe_url", return_value="https://example.com/app"):
                with self.assertRaises(ScraperError) as ctx:
                    scrape_with_browser("https://example.com/app")
                self.assertIn("exceeds maximum allowed size", str(ctx.exception))
                mock_context.close.assert_called_once()
                mock_browser.close.assert_called_once()

    # -------------------------------------------------------------------------
    # C. Rendered DOM HTML Limit Tests
    # -------------------------------------------------------------------------

    def test_rendered_dom_html_under_10_mib(self):
        """Rendered DOM HTML under 10 MiB is successfully returned."""
        mock_page = MagicMock()
        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_pw = MagicMock()
        mock_pw.chromium.launch.return_value = mock_browser
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_pw

        # 9 MiB rendered DOM HTML
        dom_content = "<html><body>" + ("P" * (9 * 1024 * 1024)) + "</body></html>"
        mock_page.content.return_value = dom_content
        mock_page.url = "https://example.com/dom9"

        with patch("playwright.sync_api.sync_playwright", return_value=mock_cm):
            with patch("scraper.validate_safe_url", return_value="https://example.com/dom9"):
                html = scrape_with_browser("https://example.com/dom9")
                self.assertEqual(len(html.encode("utf-8")), len(dom_content.encode("utf-8")))

    def test_rendered_dom_html_above_10_mib(self):
        """Rendered DOM HTML exceeding 10 MiB raises ScraperError."""
        mock_page = MagicMock()
        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_pw = MagicMock()
        mock_pw.chromium.launch.return_value = mock_browser
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_pw

        # 11 MiB rendered DOM HTML
        oversized_dom = "<html><body>" + ("Q" * (11 * 1024 * 1024)) + "</body></html>"
        mock_page.content.return_value = oversized_dom
        mock_page.url = "https://example.com/dom11"

        with patch("playwright.sync_api.sync_playwright", return_value=mock_cm):
            with patch("scraper.validate_safe_url", return_value="https://example.com/dom11"):
                with self.assertRaises(ScraperError) as ctx:
                    scrape_with_browser("https://example.com/dom11")
                self.assertIn("exceeds maximum allowed size", str(ctx.exception))

    # -------------------------------------------------------------------------
    # D. Extracted Content Limit Tests
    # -------------------------------------------------------------------------

    @patch("scraper.scrape_with_requests")
    def test_extracted_content_under_10_mib(self, mock_scrape_req):
        """Extracted article text under 10 MiB completes scrape_article successfully."""
        normal_html = "<html><head><title>Test Page</title></head><body><article><p>" + ("Text " * 1000) + "</p></article></body></html>"
        mock_scrape_req.return_value = normal_html

        result = scrape_article("https://example.com/normal")
        self.assertIn("Page Title: Test Page", result)
        self.assertIn("Text", result)

    @patch("scraper.scrape_with_requests")
    def test_extracted_content_exceeding_10_mib(self, mock_scrape_req):
        """Extracted article text exceeding 10 MiB raises ScraperError."""
        # Create HTML whose readable extracted text exceeds 10 MiB
        huge_text = "Important content block. " * (450 * 1024)  # ~11.2 MB of text
        huge_html = f"<html><head><title>Huge Page</title></head><body><article><p>{huge_text}</p></article></body></html>"
        mock_scrape_req.return_value = huge_html

        with self.assertRaises(ScraperError) as ctx:
            scrape_article("https://example.com/huge-article")
        self.assertIn("Extracted article content exceeds maximum allowed size", str(ctx.exception))

    # -------------------------------------------------------------------------
    # E. Summarizer Runaway Guard Tests
    # -------------------------------------------------------------------------

    def test_summarizer_chunk_limit_guard(self):
        """Article producing > MAX_SUMMARIZER_CHUNKS (100) chunks raises SummarizerError immediately."""
        # 105 chunks of 3500 chars = ~367,500 characters
        oversized_article = "Section of article text that has significant length. " * 7500

        with patch("summarizer.chunk_text") as mock_chunk:
            # Simulate chunker generating 105 chunks
            mock_chunk.return_value = ["mock chunk"] * 105
            with self.assertRaises(SummarizerError) as ctx:
                summarize_text(oversized_article, api_key="dummy_key")
            self.assertIn("exceeds maximum summarization capacity", str(ctx.exception))
            self.assertIn("105 chunks generated", str(ctx.exception))

    # -------------------------------------------------------------------------
    # F. Flask Incoming Request-Body Limit (MAX_CONTENT_LENGTH) Tests
    # -------------------------------------------------------------------------

    def test_flask_incoming_request_body_retains_2_mib_limit(self):
        """Verify Flask app.config['MAX_CONTENT_LENGTH'] remains 2 MiB (2,097,152 bytes)."""
        self.assertEqual(app.config["MAX_CONTENT_LENGTH"], 2 * 1024 * 1024)

    def test_flask_incoming_request_body_exceeding_2_mib_rejected(self):
        """Incoming client POST payload > 2 MiB returns 413 Payload Too Large."""
        client = app.test_client()

        # Send 2.5 MiB request payload
        oversized_body = b"A" * int(2.5 * 1024 * 1024)
        response = client.post(
            "/api/save-key",
            data=oversized_body,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 413)

    def test_flask_incoming_request_body_under_2_mib_accepted(self):
        """Incoming client POST payload under 2 MiB is processed normally."""
        client = app.test_client()

        # Normal small payload (e.g. 500 bytes)
        small_body = b'{"gemini_api_key": "test_key"}'
        response = client.post(
            "/api/save-key",
            data=small_body,
            content_type="application/json",
        )
        # Even if vault is mocked/disconnected, status code must not be 413
        self.assertNotEqual(response.status_code, 413)

    # -------------------------------------------------------------------------
    # G. Edge Cases & Resilience Tests
    # -------------------------------------------------------------------------

    @patch("requests.Session")
    @patch("scraper.resolve_and_validate_safe_url")
    def test_static_malformed_content_length_header(self, mock_validate, mock_session_cls):
        """Unparseable or malformed Content-Length does not crash, falls back to streaming count."""
        mock_validate.return_value = ("https://example.com/bad-cl", "93.184.216.34")
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        mock_resp = MagicMock()
        mock_resp.is_redirect = False
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Length": "not-a-valid-number"}
        mock_resp.encoding = "utf-8"
        mock_resp.iter_content.return_value = [b"Safe content chunk"]
        mock_session.get.return_value = mock_resp

        result = scrape_with_requests("https://example.com/bad-cl")
        self.assertEqual(result, "Safe content chunk")

    @patch("requests.Session")
    @patch("scraper.resolve_and_validate_safe_url")
    def test_static_streaming_connection_error_during_oversized_download(self, mock_validate, mock_session_cls):
        """Connection drop or stream interruption cleanly surfaces as ScraperError."""
        import requests as req
        mock_validate.return_value = ("https://example.com/drop", "93.184.216.34")
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        mock_resp = MagicMock()
        mock_resp.is_redirect = False
        mock_resp.status_code = 200
        mock_resp.headers = {}
        def fault_generator():
            yield b"First chunk"
            raise req.exceptions.ChunkedEncodingError("Connection broken mid-stream")

        mock_resp.iter_content.return_value = fault_generator()
        mock_session.get.return_value = mock_resp

        with self.assertRaises(ScraperError) as ctx:
            scrape_with_requests("https://example.com/drop")
        self.assertIn("Failed to fetch webpage", str(ctx.exception))
        self.assertIn("Connection broken mid-stream", str(ctx.exception))

    @patch("scraper.scrape_with_requests")
    def test_extracted_content_boundary_exact_vs_exceeded(self, mock_scrape_req):
        """Extracted content exactly at 10 MiB passes; 10 MiB + 1 byte fails."""
        # Exact 10 MiB
        exact_bytes = self.ten_mib - len("Page Title: T\n\n".encode("utf-8"))
        exact_text = "A" * exact_bytes
        mock_scrape_req.return_value = f"<html><head><title>T</title></head><body><article><p>{exact_text}</p></article></body></html>"

        result = scrape_article("https://example.com/exact-article")
        self.assertEqual(len(result.encode("utf-8")), self.ten_mib)

        # Exceeded by 10 bytes
        over_text = "A" * (exact_bytes + 10)
        mock_scrape_req.return_value = f"<html><head><title>T</title></head><body><article><p>{over_text}</p></article></body></html>"
        with self.assertRaises(ScraperError) as ctx:
            scrape_article("https://example.com/over-article")
        self.assertIn("Extracted article content exceeds maximum allowed size", str(ctx.exception))

    def test_env_override_scraper_limits(self):
        """Verifies environment variables override default scraper limits in a fresh process."""
        import subprocess
        import sys
        code = (
            "import os, scraper; "
            "assert scraper.MAX_RESPONSE_BYTES == 20971520; "
            "assert scraper.MAX_BROWSER_NETWORK_BYTES == 15728640; "
            "assert scraper.MAX_RENDERED_HTML_BYTES == 16777216; "
            "assert scraper.MAX_EXTRACTED_CONTENT_BYTES == 12582912; "
            "print('ENV_OVERRIDE_OK')"
        )
        env = dict(os.environ)
        env["MAX_RESPONSE_BYTES"] = "20971520"
        env["MAX_BROWSER_NETWORK_BYTES"] = "15728640"
        env["MAX_RENDERED_HTML_BYTES"] = "16777216"
        env["MAX_EXTRACTED_CONTENT_BYTES"] = "12582912"
        res = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("ENV_OVERRIDE_OK", res.stdout)


if __name__ == "__main__":
    unittest.main()
