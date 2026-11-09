"""
WebBrief AI — Google Gemini API Migration Test Suite.
Validates GeminiProvider, AIRouter, BYOK validation, structured summarization,
batch processing, comparative analysis, and vector embeddings using mocked genai.Client.
"""

import os
import sys

# Ensure root directory is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from unittest.mock import patch, MagicMock
import json

# Set test environment
os.environ["FLASK_ENV"] = "development"

from services.providers.gemini import GeminiProvider
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
from services.providers.catalogue import (
    PROVIDER_CATALOGUE,
    get_models_for_provider,
    get_default_model,
)
from summarizer import summarize_text, parse_and_validate_summary
from comparative_analyzer import compare_articles
from batch_processor import execute_batch
from byok import (
    validate_gemini_credentials,
    get_active_provider_config,
    has_valid_config,
    redact_credentials,
    save_provider_config_to_env,
    clear_saved_provider_config,
)
from vector_store import generate_embedding
from app import app


class TestGeminiProvider(unittest.TestCase):

    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    # -------------------------------------------------------------
    # 1. Gemini Successful Completion Request
    # -------------------------------------------------------------
    @patch("services.providers.gemini.genai.Client")
    def test_01_gemini_successful_completion(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.text = json.dumps({"title": "Test Title", "overview": "Test Overview"})
        mock_client.models.generate_content.return_value = mock_resp

        provider = GeminiProvider(api_key="AIzaSyTestKey12345678901234567890")
        res = provider.generate_completion(messages=[{"role": "user", "content": "Summarize"}])

        self.assertIn("Test Overview", res)
        self.assertTrue(mock_client.models.generate_content.called)

    # -------------------------------------------------------------
    # 2. Gemini Zero-Token Verification
    # -------------------------------------------------------------
    @patch("services.providers.gemini.genai.Client")
    def test_02_gemini_verify_credentials_success(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.models.get.return_value = MagicMock(name="gemini-3.8-flash")

        provider = GeminiProvider(api_key="AIzaSyTestKey12345678901234567890")
        valid, msg = provider.test_connection()

        self.assertTrue(valid)
        self.assertIn("verified", msg.lower())
        mock_client.models.get.assert_called_with(model="gemini-3.8-flash")

    # -------------------------------------------------------------
    # 3. Gemini Authentication Failure
    # -------------------------------------------------------------
    @patch("services.providers.gemini.genai.Client")
    def test_03_gemini_auth_error(self, mock_client_cls):
        from google.genai import errors
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        api_err = errors.APIError(401, {"error": {"message": "API_KEY_INVALID"}}, None)
        mock_client.models.generate_content.side_effect = api_err

        provider = GeminiProvider(api_key="AIzaSyBadKey12345")
        with self.assertRaises(ProviderAuthError):
            provider.generate_completion(messages=[{"role": "user", "content": "Hello"}])

    # -------------------------------------------------------------
    # 4. Gemini Invalid Model Failure
    # -------------------------------------------------------------
    @patch("services.providers.gemini.genai.Client")
    def test_04_gemini_invalid_model_error(self, mock_client_cls):
        from google.genai import errors
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        api_err = errors.APIError(404, {"error": {"message": "NOT_FOUND: model not found"}}, None)
        mock_client.models.generate_content.side_effect = api_err

        provider = GeminiProvider(api_key="AIzaSyTestKey", model="nonexistent-model")
        with self.assertRaises(ProviderInvalidModelError):
            provider.generate_completion(messages=[{"role": "user", "content": "Hello"}])

    # -------------------------------------------------------------
    # 5. Gemini Rate Limit (429) Handling
    # -------------------------------------------------------------
    @patch("services.providers.gemini.genai.Client")
    def test_05_gemini_rate_limit_error(self, mock_client_cls):
        from google.genai import errors
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        api_err = errors.APIError(429, {"error": {"message": "RESOURCE_EXHAUSTED: Rate limit exceeded"}}, None)
        mock_client.models.generate_content.side_effect = api_err

        provider = GeminiProvider(api_key="AIzaSyTestKey")
        with self.assertRaises((ProviderRateLimitError, ProviderQuotaError)):
            provider.generate_completion(messages=[{"role": "user", "content": "Hello"}])

    # -------------------------------------------------------------
    # 6. Gemini Timeout Handling
    # -------------------------------------------------------------
    @patch("services.providers.gemini.genai.Client")
    def test_06_gemini_timeout_error(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.models.generate_content.side_effect = TimeoutError("Request timed out")

        provider = GeminiProvider(api_key="AIzaSyTestKey")
        with self.assertRaises(ProviderTimeoutError):
            provider.generate_completion(messages=[{"role": "user", "content": "Hello"}])

    # -------------------------------------------------------------
    # 7. Gemini Embedding Generation
    # -------------------------------------------------------------
    @patch("services.providers.gemini.genai.Client")
    def test_07_gemini_embed_text(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_res = MagicMock()
        mock_embedding = MagicMock()
        mock_embedding.values = [0.1, 0.2, 0.3, 0.4]
        mock_res.embeddings = [mock_embedding]
        mock_client.models.embed_content.return_value = mock_res

        provider = GeminiProvider(api_key="AIzaSyTestKey")
        vec = provider.embed_text("Sample article text")

        self.assertEqual(vec, [0.1, 0.2, 0.3, 0.4])
        mock_client.models.embed_content.assert_called_with(
            model="gemini-embedding-001",
            contents="Sample article text"
        )

    # -------------------------------------------------------------
    # 8. AIRouter Dispatches to GeminiProvider
    # -------------------------------------------------------------
    @patch("services.providers.gemini.genai.Client")
    def test_08_ai_router_gemini_execution(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.text = '{"title": "Summary", "overview": "Overview"}'
        mock_client.models.generate_content.return_value = mock_resp

        provider = GeminiProvider(api_key="AIzaSyTestKey123")
        router = AIRouter(primary_provider=provider)
        result = router.generate_completion(messages=[{"role": "user", "content": "Test"}])

        self.assertIsInstance(result, RouterResult)
        self.assertEqual(result.provider_id, "gemini")
        self.assertEqual(result.provider_name, "Google Gemini")
        self.assertEqual(result.model, "gemini-3.8-flash")
        self.assertFalse(result.switched_to_fallback)

    # -------------------------------------------------------------
    # 9. BYOK Credential Validation Function
    # -------------------------------------------------------------
    @patch("services.providers.gemini.genai.Client")
    def test_09_validate_gemini_credentials(self, mock_client_cls):
        # Empty key rejection
        valid, msg = validate_gemini_credentials("")
        self.assertFalse(valid)
        self.assertIn("enter", msg.lower())

        # Short key rejection
        valid, msg = validate_gemini_credentials("short")
        self.assertFalse(valid)
        self.assertIn("invalid", msg.lower())

        # Mocked valid verification
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.models.get.return_value = MagicMock()

        valid, msg = validate_gemini_credentials("AIzaSyValidTestKey1234567890")
        self.assertTrue(valid)

    # -------------------------------------------------------------
    # 10. Provider Catalogue Verification
    # -------------------------------------------------------------
    def test_10_catalogue_gemini_models(self):
        self.assertIn("gemini", PROVIDER_CATALOGUE)
        self.assertNotIn("cloudflare", PROVIDER_CATALOGUE)
        self.assertNotIn("groq", PROVIDER_CATALOGUE)

        models = get_models_for_provider("gemini")
        self.assertGreater(len(models), 0)
        default_model = get_default_model("gemini")
        self.assertEqual(default_model, "gemini-3.8-flash")

        model_ids = [m["id"] for m in models]
        self.assertIn("gemini-3.8-flash", model_ids)
        self.assertIn("gemini-3.5-flash-lite", model_ids)
        self.assertNotIn("gemini-4-argon", model_ids)

    # -------------------------------------------------------------
    # 11. Parse & Validate Structured JSON Summary
    # -------------------------------------------------------------
    def test_11_parse_and_validate_summary(self):
        sample_json = json.dumps({
            "title": "Quantum Leap in AI",
            "overview": "Scientists report new breakthroughs in quantum artificial intelligence.",
            "key_points": ["10x speedup", "Lower energy consumption"],
            "core_summary": ["Detailed explanation of qubit stabilization."]
        })

        parsed = parse_and_validate_summary(sample_json)
        self.assertEqual(parsed["title"], "Quantum Leap in AI")
        self.assertIn("breakthroughs", parsed["overview"])
        self.assertEqual(len(parsed["key_points"]), 2)

    # -------------------------------------------------------------
    # 12. Full Summarization Workflow Integration
    # -------------------------------------------------------------
    @patch("services.providers.gemini.genai.Client")
    def test_12_summarize_text_workflow(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.text = json.dumps({
            "title": "Automated Summary",
            "overview": "A clear overview of the article.",
            "key_takeaways": ["Point 1", "Point 2"],
            "core_summary": ["Important detail"]
        })
        mock_client.models.generate_content.return_value = mock_resp

        res = summarize_text(
            "This is article text to summarize.",
            mode="quick",
            provider_config={"gemini_api_key": "AIzaSyTestKey12345", "gemini_model": "gemini-3.8-flash"}
        )

        self.assertEqual(res["title"], "Automated Summary")
        self.assertEqual(res["provider"], "gemini")
        self.assertEqual(res["model"], "gemini-3.8-flash")

    # -------------------------------------------------------------
    # 13. Comparative Analysis Workflow Integration
    # -------------------------------------------------------------
    @patch("comparative_analyzer.scrape_article")
    @patch("services.providers.gemini.genai.Client")
    def test_13_compare_articles_workflow(self, mock_client_cls, mock_scrape):
        long_text_a = "Source A article content on solar energy. " * 15
        long_text_b = "Source B article content on solar energy advancements. " * 15
        mock_scrape.side_effect = [long_text_a, long_text_b]
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.text = json.dumps({
            "title": "Solar Energy Comparison",
            "article_a": {"title": "Solar A", "overview": "Source A overview"},
            "article_b": {"title": "Solar B", "overview": "Source B overview"},
            "common_points": ["Solar is clean energy"],
            "differing_perspectives": ["Source A emphasizes cost, Source B efficiency"],
            "key_facts_claims": [],
            "synthesis": "Both agree solar is expanding."
        })
        mock_client.models.generate_content.return_value = mock_resp

        res = compare_articles(
            url_a="https://example.com/solar-a",
            url_b="https://example.com/solar-b",
            provider_config={"gemini_api_key": "AIzaSyTestKey12345"}
        )

        self.assertEqual(res["title"], "Solar Energy Comparison")
        self.assertEqual(res["provider"], "gemini")

    # -------------------------------------------------------------
    # 14. Batch Processing Workflow Integration
    # -------------------------------------------------------------
    @patch("batch_processor.scrape_article")
    @patch("services.providers.gemini.genai.Client")
    def test_14_batch_processor_workflow(self, mock_client_cls, mock_scrape):
        mock_scrape.return_value = "Article content for batch processing."
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.text = json.dumps({
            "title": "Batch Item",
            "overview": "Item overview",
            "key_takeaways": ["Takeaway 1"]
        })
        mock_client.models.generate_content.return_value = mock_resp

        res = execute_batch(
            raw_urls=["https://example.com/item1"],
            mode="quick",
            provider_config={"gemini_api_key": "AIzaSyTestKey12345"}
        )

        self.assertEqual(res["total"], 1)
        self.assertEqual(res["completed"], 1)
        self.assertEqual(res["results"][0]["status"], "completed")

    # -------------------------------------------------------------
    # 15. Vector Store Embedding Integration
    # -------------------------------------------------------------
    @patch("services.providers.gemini.genai.Client")
    def test_15_vector_store_embedding(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_res = MagicMock()
        mock_emb = MagicMock()
        mock_emb.values = [0.05] * 768
        mock_res.embeddings = [mock_emb]
        mock_client.models.embed_content.return_value = mock_res

        vec = generate_embedding(
            "Test text to embed into vector space",
            provider_config={"gemini_api_key": "AIzaSyTestKey12345"}
        )

        self.assertEqual(len(vec), 768)

    # -------------------------------------------------------------
    # 16. Vector Store Offline Deterministic Fallback
    # -------------------------------------------------------------
    def test_16_vector_store_deterministic_fallback(self):
        # When no API key is supplied, deterministic hash is generated
        vec = generate_embedding("Deterministic text without credentials", provider_config={})
        self.assertEqual(len(vec), 768)
        self.assertIsInstance(vec[0], float)

    # -------------------------------------------------------------
    # 17. API Endpoint: /api/validate-key
    # -------------------------------------------------------------
    @patch("app.validate_gemini_credentials", return_value=(True, "Google Gemini credentials successfully verified."))
    def test_17_api_validate_key(self, mock_val):
        with self.client.session_transaction() as sess:
            csrf = sess.get("_csrf_token", "test_csrf")
            sess["_csrf_token"] = csrf

        res = self.client.post(
            "/api/validate-key",
            json={"provider": "gemini", "gemini_api_key": "AIzaSyTestKey123"},
            headers={"X-CSRF-Token": csrf}
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["valid"])

    # -------------------------------------------------------------
    # 18. API Endpoint: /api/providers/models
    # -------------------------------------------------------------
    def test_18_api_provider_models(self):
        res = self.client.get("/api/providers/models?provider=gemini")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["provider"], "gemini")
        self.assertEqual(data["default_model"], "gemini-3.8-flash")
        self.assertGreater(len(data["models"]), 0)

    # -------------------------------------------------------------
    # 19. BYOK Environment Save & Clear Logic
    # -------------------------------------------------------------
    def test_19_save_and_clear_provider_config(self):
        test_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.test_tmp")
        try:
            success, msg = save_provider_config_to_env(
                config_dict={"gemini_api_key": "AIzaSyTestSaveKey12345", "gemini_model": "gemini-3.8-flash"},
                env_path=test_env_path,
                confirm_overwrite=True
            )
            self.assertTrue(success)

            with open(test_env_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("GEMINI_API_KEY=AIzaSyTestSaveKey12345", content)
            self.assertIn("GEMINI_DEFAULT_MODEL=gemini-3.8-flash", content)

            # Clear
            clear_saved_provider_config(env_path=test_env_path)
            with open(test_env_path, "r", encoding="utf-8") as f:
                cleared_content = f.read()
            self.assertNotIn("AIzaSyTestSaveKey12345", cleared_content)
        finally:
            if os.path.exists(test_env_path):
                os.remove(test_env_path)

    # -------------------------------------------------------------
    # 20. Active Provider Config Resolution
    # -------------------------------------------------------------
    def test_20_get_active_provider_config(self):
        cfg = get_active_provider_config(
            request_data={"gemini_api_key": "AIzaSyExplicitOverride", "gemini_model": "gemini-3.5-flash-lite"}
        )
        self.assertEqual(cfg["gemini_api_key"], "AIzaSyExplicitOverride")
        self.assertEqual(cfg["gemini_model"], "gemini-3.5-flash-lite")
        self.assertEqual(cfg["primary_provider"], "gemini")


if __name__ == "__main__":
    unittest.main()
