"""
WebBrief AI — v1.6.0 Multi-Provider & Automatic Fallback Routing Test Suite.
Validates all 20 test specifications from Phase 10 with mocked HTTP interactions.
"""

import os
import sys

# Ensure root directory is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from unittest.mock import patch, MagicMock
import json
import requests

# Set test environment
os.environ["FLASK_ENV"] = "development"

from services.providers.cloudflare import CloudflareProvider
from services.providers.groq import GroqProvider
from services.providers.router import AIRouter, RouterResult
from services.providers.exceptions import (
    ProviderError,
    ProviderAuthError,
    ProviderInvalidModelError,
    ProviderBadRequestError,
    ProviderRateLimitError,
    ProviderQuotaError,
    ProviderUnavailableError,
    ProviderTimeoutError,
)
from services.providers.catalogue import PROVIDER_CATALOGUE, get_models_for_provider
from summarizer import summarize_text, parse_and_validate_summary
from comparative_analyzer import compare_articles
from batch_processor import execute_batch
from byok import (
    validate_cloudflare_credentials,
    validate_groq_credentials,
    get_active_provider_config,
    has_valid_config,
    redact_credentials,
    is_serverless_env,
)
from app import app


class TestV160MultiProvider(unittest.TestCase):

    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        self.dns_patcher = patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))])
        self.dns_patcher.start()

    def tearDown(self):
        self.dns_patcher.stop()

    # -------------------------------------------------------------
    # 1. Cloudflare Successful Request
    # -------------------------------------------------------------
    @patch("requests.post")
    def test_01_cloudflare_successful_request(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "success": True,
            "result": {"response": "This is a concise Cloudflare Workers AI summary."},
            "errors": [],
            "messages": []
        }
        mock_post.return_value = mock_resp

        provider = CloudflareProvider(account_id="dummy_cf_account", api_token="dummy_cf_token")
        res = provider.generate_completion(messages=[{"role": "user", "content": "Summarize this"}])

        self.assertEqual(res, "This is a concise Cloudflare Workers AI summary.")
        self.assertTrue(mock_post.called)
        call_url = mock_post.call_args[0][0]
        self.assertIn("api.cloudflare.com/client/v4/accounts/dummy_cf_account/ai/run", call_url)

    # -------------------------------------------------------------
    # 2. Groq Successful Request
    # -------------------------------------------------------------
    @patch("requests.post")
    def test_02_groq_successful_request(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "This is a blazing fast GroqCloud summary."}}]
        }
        mock_post.return_value = mock_resp

        provider = GroqProvider(api_key="gsk_dummy_groq_test_key_123456789")
        res = provider.generate_completion(messages=[{"role": "user", "content": "Summarize this"}])

        self.assertEqual(res, "This is a blazing fast GroqCloud summary.")
        self.assertTrue(mock_post.called)
        call_url = mock_post.call_args[0][0]
        self.assertEqual(call_url, "https://api.groq.com/openai/v1/chat/completions")

    # -------------------------------------------------------------
    # 3. Primary Provider Success
    # -------------------------------------------------------------
    @patch("services.providers.cloudflare.CloudflareProvider.generate_completion")
    def test_03_primary_provider_success(self, mock_cf):
        mock_cf.return_value = "Primary success output."

        router = AIRouter(
            primary_provider="cloudflare",
            primary_credentials={"account_id": "cf_id", "api_token": "cf_token"},
            fallback_enabled=True,
            fallback_provider="groq",
            fallback_credentials={"api_key": "gsk_123"},
        )
        res = router.generate_completion(messages=[{"role": "user", "content": "test"}])

        self.assertEqual(res.content, "Primary success output.")
        self.assertEqual(res.provider_id, "cloudflare")
        self.assertFalse(res.switched_to_fallback)

    # -------------------------------------------------------------
    # 4. Primary 429 Followed by Fallback Success
    # -------------------------------------------------------------
    @patch("services.providers.cloudflare.CloudflareProvider.generate_completion")
    @patch("services.providers.groq.GroqProvider.generate_completion")
    def test_04_primary_429_followed_by_fallback_success(self, mock_groq, mock_cf):
        mock_cf.side_effect = ProviderRateLimitError("Rate limit reached (429)", provider="cloudflare")
        mock_groq.return_value = "Fallback Groq recovered output."

        router = AIRouter(
            primary_provider="cloudflare",
            primary_credentials={"account_id": "cf_id", "api_token": "cf_token"},
            fallback_enabled=True,
            fallback_provider="groq",
            fallback_credentials={"api_key": "gsk_123"},
        )
        res = router.generate_completion(messages=[{"role": "user", "content": "test"}])

        self.assertEqual(res.content, "Fallback Groq recovered output.")
        self.assertEqual(res.provider_id, "groq")
        self.assertTrue(res.switched_to_fallback)
        self.assertIn("Rate limit reached", res.fallback_reason)

    # -------------------------------------------------------------
    # 5. Primary Quota Exhaustion Followed by Fallback Success
    # -------------------------------------------------------------
    @patch("services.providers.cloudflare.CloudflareProvider.generate_completion")
    @patch("services.providers.groq.GroqProvider.generate_completion")
    def test_05_primary_quota_exhaustion_followed_by_fallback_success(self, mock_groq, mock_cf):
        mock_cf.side_effect = ProviderQuotaError("Neuron daily quota exhausted", provider="cloudflare")
        mock_groq.return_value = "Recovered via Groq after Cloudflare quota exhaustion."

        router = AIRouter(
            primary_provider="cloudflare",
            primary_credentials={"account_id": "cf_id", "api_token": "cf_token"},
            fallback_enabled=True,
            fallback_provider="groq",
            fallback_credentials={"api_key": "gsk_123"},
        )
        res = router.generate_completion(messages=[{"role": "user", "content": "test"}])

        self.assertEqual(res.content, "Recovered via Groq after Cloudflare quota exhaustion.")
        self.assertEqual(res.provider_id, "groq")
        self.assertTrue(res.switched_to_fallback)

    # -------------------------------------------------------------
    # 6. Primary 503 Followed by Fallback Success
    # -------------------------------------------------------------
    @patch("services.providers.cloudflare.CloudflareProvider.generate_completion")
    @patch("services.providers.groq.GroqProvider.generate_completion")
    def test_06_primary_503_followed_by_fallback_success(self, mock_groq, mock_cf):
        mock_cf.side_effect = ProviderUnavailableError("Cloudflare service outage (HTTP 503)", provider="cloudflare")
        mock_groq.return_value = "Recovered via Groq after 503."

        router = AIRouter(
            primary_provider="cloudflare",
            primary_credentials={"account_id": "cf_id", "api_token": "cf_token"},
            fallback_enabled=True,
            fallback_provider="groq",
            fallback_credentials={"api_key": "gsk_123"},
        )
        res = router.generate_completion(messages=[{"role": "user", "content": "test"}])

        self.assertEqual(res.content, "Recovered via Groq after 503.")
        self.assertTrue(res.switched_to_fallback)

    # -------------------------------------------------------------
    # 7. Invalid Primary Key Without Automatic Fallback
    # -------------------------------------------------------------
    @patch("services.providers.cloudflare.CloudflareProvider.generate_completion")
    @patch("services.providers.groq.GroqProvider.generate_completion")
    def test_07_invalid_primary_key_without_automatic_fallback(self, mock_groq, mock_cf):
        mock_cf.side_effect = ProviderAuthError("Invalid API token (401)", provider="cloudflare")

        router = AIRouter(
            primary_provider="cloudflare",
            primary_credentials={"account_id": "cf_id", "api_token": "cf_token"},
            fallback_enabled=True,
            fallback_provider="groq",
            fallback_credentials={"api_key": "gsk_123"},
        )
        # Authentication errors MUST NOT automatically fall back
        with self.assertRaises(ProviderAuthError):
            router.generate_completion(messages=[{"role": "user", "content": "test"}])

        # Verify fallback was NEVER called
        self.assertFalse(mock_groq.called)

    # -------------------------------------------------------------
    # 8. Invalid Fallback Credentials
    # -------------------------------------------------------------
    @patch("services.providers.cloudflare.CloudflareProvider.generate_completion")
    @patch("services.providers.groq.GroqProvider.generate_completion")
    def test_08_invalid_fallback_credentials(self, mock_groq, mock_cf):
        mock_cf.side_effect = ProviderRateLimitError("Cloudflare 429", provider="cloudflare")
        mock_groq.side_effect = ProviderAuthError("Groq 401 unauthorized", provider="groq")

        router = AIRouter(
            primary_provider="cloudflare",
            primary_credentials={"account_id": "cf_id", "api_token": "cf_token"},
            fallback_enabled=True,
            fallback_provider="groq",
            fallback_credentials={"api_key": "invalid_groq_key"},
        )
        with self.assertRaises(ProviderError) as ctx:
            router.generate_completion(messages=[{"role": "user", "content": "test"}])

        self.assertIn("All configured providers failed", str(ctx.exception))

    # -------------------------------------------------------------
    # 9. Both Providers Unavailable
    # -------------------------------------------------------------
    @patch("services.providers.cloudflare.CloudflareProvider.generate_completion")
    @patch("services.providers.groq.GroqProvider.generate_completion")
    def test_09_both_providers_unavailable(self, mock_groq, mock_cf):
        mock_cf.side_effect = ProviderUnavailableError("Cloudflare 503 Service Unavailable", provider="cloudflare")
        mock_groq.side_effect = ProviderUnavailableError("Groq 503 Service Unavailable", provider="groq")

        router = AIRouter(
            primary_provider="cloudflare",
            primary_credentials={"account_id": "cf_id", "api_token": "cf_token"},
            fallback_enabled=True,
            fallback_provider="groq",
            fallback_credentials={"api_key": "gsk_123"},
        )
        with self.assertRaises(ProviderError) as ctx:
            router.generate_completion(messages=[{"role": "user", "content": "test"}])

        self.assertIn("All configured providers failed", str(ctx.exception))

    # -------------------------------------------------------------
    # 10. Fallback Disabled
    # -------------------------------------------------------------
    @patch("services.providers.cloudflare.CloudflareProvider.generate_completion")
    @patch("services.providers.groq.GroqProvider.generate_completion")
    def test_10_fallback_disabled(self, mock_groq, mock_cf):
        mock_cf.side_effect = ProviderRateLimitError("Rate limit reached", provider="cloudflare")

        router = AIRouter(
            primary_provider="cloudflare",
            primary_credentials={"account_id": "cf_id", "api_token": "cf_token"},
            fallback_enabled=False,
            fallback_provider="groq",
            fallback_credentials={"api_key": "gsk_123"},
        )
        with self.assertRaises(ProviderRateLimitError):
            router.generate_completion(messages=[{"role": "user", "content": "test"}])

        self.assertFalse(mock_groq.called)

    # -------------------------------------------------------------
    # 11. No Fallback Configured
    # -------------------------------------------------------------
    @patch("services.providers.cloudflare.CloudflareProvider.generate_completion")
    def test_11_no_fallback_configured(self, mock_cf):
        mock_cf.side_effect = ProviderRateLimitError("Rate limit reached", provider="cloudflare")

        router = AIRouter(
            primary_provider="cloudflare",
            primary_credentials={"account_id": "cf_id", "api_token": "cf_token"},
            fallback_enabled=False,
            fallback_provider=None,
        )
        self.assertIsNone(router.fallback)
        with self.assertRaises(ProviderRateLimitError):
            router.generate_completion(messages=[{"role": "user", "content": "test"}])

    # -------------------------------------------------------------
    # 12. Invalid Model Selection
    # -------------------------------------------------------------
    @patch("services.providers.cloudflare.CloudflareProvider.generate_completion")
    @patch("services.providers.groq.GroqProvider.generate_completion")
    def test_12_invalid_model_selection(self, mock_groq, mock_cf):
        mock_cf.side_effect = ProviderInvalidModelError("Model '@cf/invalid/nonexistent' not found", provider="cloudflare")

        router = AIRouter(
            primary_provider="cloudflare",
            primary_credentials={"account_id": "cf_id", "api_token": "cf_token"},
            fallback_enabled=True,
            fallback_provider="groq",
            fallback_credentials={"api_key": "gsk_123"},
        )
        # Invalid model MUST NOT fall back
        with self.assertRaises(ProviderInvalidModelError):
            router.generate_completion(messages=[{"role": "user", "content": "test"}])

        self.assertFalse(mock_groq.called)

    # -------------------------------------------------------------
    # 13. Timeout Handling
    # -------------------------------------------------------------
    @patch("services.providers.cloudflare.CloudflareProvider.generate_completion")
    @patch("services.providers.groq.GroqProvider.generate_completion")
    def test_13_timeout_handling(self, mock_groq, mock_cf):
        mock_cf.side_effect = ProviderTimeoutError("Inference request timed out after 45s", provider="cloudflare")
        mock_groq.return_value = "Recovered after primary timeout."

        router = AIRouter(
            primary_provider="cloudflare",
            primary_credentials={"account_id": "cf_id", "api_token": "cf_token"},
            fallback_enabled=True,
            fallback_provider="groq",
            fallback_credentials={"api_key": "gsk_123"},
        )
        res = router.generate_completion(messages=[{"role": "user", "content": "test"}])
        self.assertEqual(res.content, "Recovered after primary timeout.")
        self.assertTrue(res.switched_to_fallback)

    # -------------------------------------------------------------
    # 14. Credential Isolation
    # -------------------------------------------------------------
    def test_14_credential_isolation(self):
        with patch.dict(os.environ, {"VERCEL": "1", "GROQ_API_KEY": "gsk_server_dev_key_should_not_leak"}):
            # In serverless/hosted mode, server-side environment credentials must NOT be exposed to visitors
            cfg = get_active_provider_config()
            self.assertEqual(cfg["groq_api_key"], "")
            self.assertFalse(has_valid_config())

            # Only per-request visitor credentials can be used
            req_cfg = get_active_provider_config(request_data={"groq_api_key": "gsk_visitor_key_1234567890", "primary_provider": "groq"})
            self.assertEqual(req_cfg["groq_api_key"], "gsk_visitor_key_1234567890")
            self.assertTrue(has_valid_config(request_data=req_cfg))

    # -------------------------------------------------------------
    # 15. No Credentials in Logs or Errors
    # -------------------------------------------------------------
    def test_15_no_credentials_in_logs(self):
        raw_error = "Error connecting to Groq with key gsk_abc12345678901234567890 and Cloudflare token sk-proj-1234567890abcdef"
        sanitized = redact_credentials(raw_error)
        self.assertNotIn("gsk_abc12345678901234567890", sanitized)
        self.assertNotIn("sk-proj-1234567890abcdef", sanitized)
        self.assertIn("gsk_...", sanitized)
        self.assertIn("sk-...", sanitized)

    # -------------------------------------------------------------
    # 16. No Credentials in Browser Persistent Storage
    # -------------------------------------------------------------
    def test_16_no_credentials_in_browser_storage(self):
        # Read storage_adapter.js to verify it does not write credentials to localStorage or cookies
        with open("static/storage_adapter.js", "r", encoding="utf-8") as f:
            code = f.read()

        # Check for forbidden localStorage key storage
        self.assertNotIn("localStorage.setItem('webbrief_api_key'", code)
        self.assertNotIn("localStorage.setItem('groq_api_key'", code)
        self.assertNotIn("localStorage.setItem('cloudflare_token'", code)
        self.assertNotIn("document.cookie = `webbrief_api_key", code)

    # -------------------------------------------------------------
    # 17. Existing Summarization Functionality
    # -------------------------------------------------------------
    @patch("services.providers.router.AIRouter.generate_structured_json")
    def test_17_existing_summarization_functionality(self, mock_json):
        mock_json.return_value = RouterResult(
            content={
                "title": "Quantum Computing Breakthrough",
                "overview": "Scientists achieve quantum supremacy in error correction.",
                "key_points": ["Point 1", "Point 2"],
                "important_details": ["Detail 1"],
                "conclusion": "Commercial viability expected by 2030."
            },
            provider_id="cloudflare",
            provider_name="Cloudflare Workers AI",
            model="@cf/meta/llama-3.1-8b-instruct",
            switched_to_fallback=False
        )

        res = summarize_text(
            article_text="Quantum computing article text " * 10,
            provider_config={"cloudflare_account_id": "test_id", "cloudflare_api_token": "test_token", "primary_provider": "cloudflare"},
            mode="detailed"
        )

        self.assertEqual(res["title"], "Quantum Computing Breakthrough")
        self.assertEqual(res["mode"], "detailed")
        self.assertEqual(res["provider"], "cloudflare")
        self.assertEqual(res["model"], "@cf/meta/llama-3.1-8b-instruct")

    # -------------------------------------------------------------
    # 18. Existing Batch Processing
    # -------------------------------------------------------------
    @patch("batch_processor.validate_safe_url", side_effect=lambda u: u)
    @patch("batch_processor.scrape_article")
    @patch("services.providers.router.AIRouter.generate_structured_json")
    def test_18_existing_batch_processing(self, mock_router_json, mock_scrape, mock_val):
        mock_scrape.return_value = "Article content for testing batch processing " * 5
        mock_router_json.return_value = RouterResult(
            content={
                "title": "Batch Article",
                "overview": "Overview of batch article",
                "key_points": ["Point A"],
                "important_details": ["Detail A"],
                "conclusion": "Done"
            },
            provider_id="groq",
            provider_name="GroqCloud",
            model="llama-3.3-70b-versatile",
            switched_to_fallback=False
        )

        batch_res = execute_batch(
            raw_urls=["https://example.com/page1", "https://example.com/page2"],
            provider_config={"groq_api_key": "gsk_12345678901234567890", "primary_provider": "groq"}
        )

        self.assertEqual(len(batch_res["results"]), 2)
        self.assertEqual(batch_res["completed"], 2)
        self.assertEqual(batch_res["results"][0]["status"], "completed")

    # -------------------------------------------------------------
    # 19. Existing Comparative Summarization
    # -------------------------------------------------------------
    @patch("comparative_analyzer.validate_safe_url", side_effect=lambda u: u)
    @patch("comparative_analyzer.scrape_article")
    @patch("services.providers.router.AIRouter.generate_structured_json")
    def test_19_existing_comparative_summarization(self, mock_router_json, mock_scrape, mock_val):
        mock_scrape.side_effect = [
            "Source A detailed text on electric vehicles " * 10,
            "Source B detailed text on hydrogen vehicles " * 10,
        ]
        mock_router_json.return_value = RouterResult(
            content={
                "title": "EVs vs Hydrogen Fuel Cells",
                "article_a": {"title": "Source A", "url": "https://example.com/a", "overview": "Overview A"},
                "article_b": {"title": "Source B", "url": "https://example.com/b", "overview": "Overview B"},
                "common_points": ["Both are zero direct emissions"],
                "contradictions": ["Cost vs infrastructure requirements"],
                "differing_perspectives": ["Battery density vs charging speed"],
                "key_facts_claims": [],
                "unique_to_a": ["Battery recycling innovations"],
                "unique_to_b": ["Fuel cell stack durability"],
                "conclusions_comparison": {
                    "article_a_conclusion": "EVs dominate consumer market",
                    "article_b_conclusion": "Hydrogen excels for heavy haul",
                    "comparison_note": "Complementary use cases"
                },
                "synthesis": "Both technologies serve distinct segments."
            },
            provider_id="cloudflare",
            provider_name="Cloudflare Workers AI",
            model="@cf/meta/llama-3.1-8b-instruct",
            switched_to_fallback=False
        )

        comp_res = compare_articles(
            url_a="https://example.com/ev",
            url_b="https://example.com/hydrogen",
            provider_config={"cloudflare_account_id": "test_id", "cloudflare_api_token": "test_token"}
        )

        self.assertEqual(comp_res["title"], "EVs vs Hydrogen Fuel Cells")
        self.assertEqual(comp_res["provider"], "cloudflare")
        self.assertEqual(len(comp_res["common_points"]), 1)

    # -------------------------------------------------------------
    # 20. Local and Hosted Configuration Compatibility
    # -------------------------------------------------------------
    def test_20_local_and_hosted_configuration_compatibility(self):
        # In local mode: has_valid_config reads from environment
        with patch.dict(os.environ, {"PRIMARY_PROVIDER": "cloudflare", "CLOUDFLARE_ACCOUNT_ID": "local_id", "CLOUDFLARE_API_TOKEN": "local_token"}, clear=False):
            self.assertTrue(has_valid_config())

        # In hosted mode: environment variables are ignored for visitors
        with patch.dict(os.environ, {"VERCEL": "1", "CLOUDFLARE_ACCOUNT_ID": "local_id", "CLOUDFLARE_API_TOKEN": "local_token"}):
            self.assertFalse(has_valid_config())
            # But per-request visitor payload succeeds
            visitor_payload = {
                "primary_provider": "cloudflare",
                "cloudflare_account_id": "visitor_id",
                "cloudflare_api_token": "visitor_token"
            }
            self.assertTrue(has_valid_config(request_data=visitor_payload))


if __name__ == "__main__":
    unittest.main()
