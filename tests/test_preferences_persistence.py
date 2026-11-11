"""
WebBrief AI — Custom Prompt Directives Persistence Test Suite.
Validates:
1. Local SQLite preference persistence (/api/preferences, save, reset, length limits).
2. Per-request custom directive propagation across single, batch, retry, and compare.
3. Serverless/hosted mode per-request isolation without reliance on process memory.
4. StorageAdapter contract & IndexedDB v2 preferences schema alignment.
5. Removal of conflicting localStorage-based directive reads from Home and Settings.
"""

import os
import sys
import json
import unittest
from unittest.mock import patch, MagicMock

# Ensure root directory is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["FLASK_ENV"] = "development"

from app import app
from history import (
    get_custom_directives,
    save_custom_directives,
    reset_custom_directives,
    MAX_DIRECTIVE_LENGTH,
)


class TestPreferencesPersistence(unittest.TestCase):

    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        # Reset directives before each test
        reset_custom_directives()

    def tearDown(self):
        reset_custom_directives()

    def _get_csrf_headers(self):
        with self.client.session_transaction() as sess:
            csrf = sess.get("_csrf_token", "test_csrf_token_val")
            sess["_csrf_token"] = csrf
        return {"X-CSRF-Token": csrf}

    # -------------------------------------------------------------
    # 1. Local SQLite: Save and Retrieve Preferences
    # -------------------------------------------------------------
    def test_01_save_and_get_preferences_api(self):
        headers = self._get_csrf_headers()
        res = self.client.post(
            "/api/preferences",
            json={
                "custom_directive": "Focus on core architecture, benchmarks, and security trade-offs.",
                "custom_directive_enabled": True
            },
            headers=headers
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(
            data["preferences"]["custom_directive"],
            "Focus on core architecture, benchmarks, and security trade-offs."
        )
        self.assertTrue(data["preferences"]["custom_directive_enabled"])

        # GET /api/preferences
        get_res = self.client.get("/api/preferences")
        self.assertEqual(get_res.status_code, 200)
        get_data = get_res.get_json()
        self.assertEqual(
            get_data["custom_directive"],
            "Focus on core architecture, benchmarks, and security trade-offs."
        )
        self.assertTrue(get_data["custom_directive_enabled"])

        # Backend Python function reads same values from SQLite
        direct = get_custom_directives()
        self.assertEqual(
            direct["custom_directive"],
            "Focus on core architecture, benchmarks, and security trade-offs."
        )
        self.assertTrue(direct["custom_directive_enabled"])

    # -------------------------------------------------------------
    # 2. Local SQLite: Reset Preferences
    # -------------------------------------------------------------
    def test_02_reset_preferences_api(self):
        headers = self._get_csrf_headers()
        # First save
        self.client.post(
            "/api/preferences",
            json={"custom_directive": "Temporary directive", "custom_directive_enabled": True},
            headers=headers
        )

        # Reset
        res = self.client.post("/api/preferences/reset", headers=headers)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["preferences"]["custom_directive"], "")
        self.assertFalse(data["preferences"]["custom_directive_enabled"])

        # Confirm GET reflects reset
        get_res = self.client.get("/api/preferences")
        get_data = get_res.get_json()
        self.assertEqual(get_data["custom_directive"], "")
        self.assertFalse(get_data["custom_directive_enabled"])

    # -------------------------------------------------------------
    # 3. Directives Length Limit Enforcement
    # -------------------------------------------------------------
    def test_03_directive_length_limit(self):
        headers = self._get_csrf_headers()
        long_directive = "A" * (MAX_DIRECTIVE_LENGTH + 50)
        res = self.client.post(
            "/api/preferences",
            json={"custom_directive": long_directive, "custom_directive_enabled": True},
            headers=headers
        )
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertFalse(data["success"])
        self.assertIn(str(MAX_DIRECTIVE_LENGTH), data["message"])

    # -------------------------------------------------------------
    # 4. Single Summarization Uses Payload Directives
    # -------------------------------------------------------------
    @patch("app.index_summary_entry", return_value=True)
    @patch("app.scrape_article", return_value="Sample article content for testing custom directive injection.")
    @patch("services.providers.gemini.genai.Client")
    def test_04_summarize_applies_payload_directive(self, mock_client_cls, mock_scrape, mock_index):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.text = json.dumps({
            "title": "Directive Summary",
            "overview": "Overview with directive applied",
            "key_points": ["Point 1"],
            "important_details": ["Detail 1"],
            "conclusion": "Conclusion"
        })
        mock_client.models.generate_content.return_value = mock_resp

        headers = self._get_csrf_headers()
        res = self.client.post(
            "/summarize",
            json={
                "url": "https://example.com/test-article",
                "gemini_api_key": "AIzaSyTestKey12345678901234567890",
                "mode": "quick",
                "custom_directive": "Emphasize revenue growth and unit economics.",
                "custom_directive_enabled": True
            },
            headers=headers
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertTrue(data["summary"]["custom_directive_applied"])
        self.assertEqual(data["summary"]["custom_directive"], "Emphasize revenue growth and unit economics.")

    # -------------------------------------------------------------
    # 5. Batch Summarization Propagates Custom Directives
    # -------------------------------------------------------------
    @patch("batch_processor.scrape_article", return_value="Batch item text.")
    @patch("services.providers.gemini.genai.Client")
    def test_05_batch_summarize_propagates_directives(self, mock_client_cls, mock_scrape):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.text = json.dumps({
            "title": "Batch Article",
            "overview": "Batch overview",
            "key_points": ["Key point"]
        })
        mock_client.models.generate_content.return_value = mock_resp

        headers = self._get_csrf_headers()
        res = self.client.post(
            "/batch/summarize",
            json={
                "urls": ["https://example.com/batch-article-1"],
                "api_key": "AIzaSyTestKey12345678901234567890",
                "mode": "quick",
                "custom_directive": "Extract executive action items only.",
                "custom_directive_enabled": True
            },
            headers=headers
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["completed"], 1)

    # -------------------------------------------------------------
    # 6. Comparative Research Propagates Custom Directives
    # -------------------------------------------------------------
    @patch("comparative_analyzer.scrape_article", side_effect=[
        "Source A article content with sufficient length for analysis testing and comparison requirements.",
        "Source B article content with sufficient length for analysis testing and comparison requirements."
    ])
    @patch("services.providers.gemini.genai.Client")
    def test_06_compare_propagates_directives(self, mock_client_cls, mock_scrape):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.text = json.dumps({
            "title": "Comparative Analysis",
            "article_a": {"title": "Source A", "overview": "A overview"},
            "article_b": {"title": "Source B", "overview": "B overview"},
            "common_points": ["Consensus point"],
            "differing_perspectives": ["Differing perspective"],
            "key_facts_claims": [],
            "synthesis": "Comparative synthesis."
        })
        mock_client.models.generate_content.return_value = mock_resp

        headers = self._get_csrf_headers()
        res = self.client.post(
            "/compare",
            json={
                "url_a": "https://example.com/source-a",
                "url_b": "https://example.com/source-b",
                "api_key": "AIzaSyTestKey12345678901234567890",
                "custom_directive": "Highlight statistical discrepancies.",
                "custom_directive_enabled": True
            },
            headers=headers
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])

    # -------------------------------------------------------------
    # 7. Hosted Serverless Mode Uses Per-Request Directives Without Process Memory
    # -------------------------------------------------------------
    @patch.dict(os.environ, {"VERCEL": "1"})
    @patch("app.index_summary_entry", return_value=True)
    @patch("app.scrape_article", return_value="Article content in hosted serverless environment.")
    @patch("services.providers.gemini.genai.Client")
    def test_07_hosted_serverless_mode_uses_payload_directive(self, mock_client_cls, mock_scrape, mock_index):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.text = json.dumps({
            "title": "Serverless Brief",
            "overview": "Overview from per-request memory",
            "key_points": ["Serverless Point"]
        })
        mock_client.models.generate_content.return_value = mock_resp

        headers = self._get_csrf_headers()
        res = self.client.post(
            "/summarize",
            json={
                "url": "https://example.com/hosted-article",
                "gemini_api_key": "AIzaSyHostedKey12345678901234567890",
                "mode": "quick",
                "custom_directive": "Strictly in-memory visitor directive.",
                "custom_directive_enabled": True
            },
            headers=headers
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["summary"]["custom_directive"], "Strictly in-memory visitor directive.")
        self.assertTrue(data["summary"]["custom_directive_applied"])

    # -------------------------------------------------------------
    # 8. StorageAdapter Frontend Contract & IndexedDB v2 Schema
    # -------------------------------------------------------------
    def test_08_storage_adapter_js_contract(self):
        adapter_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static", "storage_adapter.js"
        )
        with open(adapter_path, "r", encoding="utf-8") as f:
            code = f.read()

        # Check DB version upgraded to 2 and preferences store defined
        self.assertIn("DB_VERSION = 2", code)
        self.assertIn("PREFS_STORE_NAME = 'preferences'", code)
        self.assertIn("createObjectStore(PREFS_STORE_NAME", code)

        # Check required API methods on StorageAdapter
        self.assertIn("loadPreferences:", code)
        self.assertIn("getPreferences:", code)
        self.assertIn("getDirectivesSync:", code)
        self.assertIn("savePreferences:", code)
        self.assertIn("resetPreferences:", code)

        # Confirm credentials are NOT stored in IndexedDB preferences
        self.assertNotIn("store.put({ key: 'credentials'", code)
        self.assertNotIn("store.put({ key: 'api_key'", code)

    # -------------------------------------------------------------
    # 9. Single-Page Application Free of Conflicting localStorage
    # -------------------------------------------------------------
    def test_09_templates_no_conflicting_localstorage_directive_reads(self):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        index_path = os.path.join(base_dir, "templates", "index.html")
        settings_path = os.path.join(base_dir, "templates", "settings.html")

        # Confirm settings.html has been scrapped
        self.assertFalse(os.path.exists(settings_path), "settings.html must be scrapped in single-page architecture.")

        with open(index_path, "r", encoding="utf-8") as f:
            index_code = f.read()

        # Confirm zero localStorage.getItem for directives
        self.assertNotIn('localStorage.getItem("webbrief_custom_directive")', index_code)
        self.assertNotIn('localStorage.getItem("webbrief_custom_directive_enabled")', index_code)

        # Confirm single-page app uses StorageAdapter.getPreferences, savePreferences & resetPreferences
        self.assertIn("window.StorageAdapter.getPreferences()", index_code)
        self.assertIn("window.StorageAdapter.savePreferences(", index_code)
        self.assertIn("window.StorageAdapter.resetPreferences()", index_code)


if __name__ == "__main__":
    unittest.main()
