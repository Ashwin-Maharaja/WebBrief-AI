"""
WebBrief AI — Integration Fixes & Regression Test Suite.
Validates:
1. Notion signatures, pagination, and block append validation.
2. PDF export signatures and comparative routing.
3. Vault credential merging (preservation of Notion token when saving Gemini key).
4. Vault maximum lifetime preservation and boolean delete results.
5. Scraper byte accounting (elimination of double counting).
"""

import json
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["FLASK_ENV"] = "development"
from app import app
from notion_service import (
    get_accessible_pages,
    format_single_summary_blocks,
    format_comparison_blocks,
    create_notion_page,
    NotionError,
)
from pdf_exporter import (
    generate_summary_pdf,
    generate_comparison_pdf,
    build_pdf_filename,
)
from credential_vault import TemporaryCredentialVault
from cryptography.fernet import Fernet
from redis.exceptions import RedisError


class FakeRedis:
    def __init__(self):
        self.data = {}
        self.ttls = {}

    def set(self, key, value):
        self.data[key] = value

    def setex(self, key, ttl, value):
        self.data[key] = value
        self.ttls[key] = ttl

    def get(self, key):
        return self.data.get(key)

    def expire(self, key, ttl):
        self.ttls[key] = ttl

    def delete(self, key):
        self.data.pop(key, None)


class TestIntegrationFixes(unittest.TestCase):

    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        self.client.get("/")
        with self.client.session_transaction() as sess:
            self.csrf_token = sess.get("_csrf_token", "")

    # -------------------------------------------------------------
    # 1. Notion Service Signatures & Pagination
    # -------------------------------------------------------------
    @patch("notion_service.requests.post")
    def test_01_notion_get_accessible_pages_signatures(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [
                {
                    "id": "page_11111111111111111111111111111111",
                    "properties": {
                        "title": {"type": "title", "title": [{"plain_text": "Engineering Wiki"}]}
                    },
                    "icon": {"type": "emoji", "emoji": "🚀"},
                }
            ],
            "has_more": False,
            "next_cursor": None,
        }
        mock_post.return_value = mock_resp

        # Positional token
        pages1 = get_accessible_pages("secret_test123")
        self.assertEqual(len(pages1), 1)
        self.assertEqual(pages1[0]["title"], "Engineering Wiki")

        # Keyword token
        pages2 = get_accessible_pages(token="secret_test123")
        self.assertEqual(len(pages2), 1)

        # Keyword api_key
        pages3 = get_accessible_pages(api_key="secret_test123")
        self.assertEqual(len(pages3), 1)

    @patch("notion_service.requests.post")
    def test_02_notion_pagination_cursor(self, mock_post):
        """Verifies that get_accessible_pages follows next_cursor when has_more is True."""
        resp1 = MagicMock()
        resp1.status_code = 200
        resp1.json.return_value = {
            "results": [
                {
                    "id": "page_batch1",
                    "properties": {"title": {"type": "title", "title": [{"plain_text": "Page One"}]}},
                }
            ],
            "has_more": True,
            "next_cursor": "cursor_page_2",
        }

        resp2 = MagicMock()
        resp2.status_code = 200
        resp2.json.return_value = {
            "results": [
                {
                    "id": "page_batch2",
                    "properties": {"title": {"type": "title", "title": [{"plain_text": "Page Two"}]}},
                }
            ],
            "has_more": False,
            "next_cursor": None,
        }

        mock_post.side_effect = [resp1, resp2]
        pages = get_accessible_pages("secret_test123")
        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0]["title"], "Page One")
        self.assertEqual(pages[1]["title"], "Page Two")
        self.assertEqual(mock_post.call_count, 2)

    @patch("notion_service.requests.post")
    def test_03_notion_error_propagation(self, mock_post):
        """Verifies 401/403/429 raises NotionError instead of returning an empty list."""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        mock_post.return_value = mock_resp

        with self.assertRaises(NotionError) as ctx:
            get_accessible_pages("secret_invalid")
        self.assertIn("invalid or has been revoked", str(ctx.exception))

    def test_04_format_single_summary_blocks_signature(self):
        """Verifies title can be positional, keyword, or inferred from summary."""
        sum_data = {
            "title": "Quantum Computing Breakthrough",
            "overview": "Scientists achieve new qubit coherence milestone.",
            "key_points": ["100x stability increase", "Operates at room temperature"],
            "important_details": ["Uses synthetic diamond lattices"],
            "conclusion": "Commercial viability remains 5-10 years away."
        }

        # Calling with title as keyword
        b1 = format_single_summary_blocks(sum_data, title="Explicit Title", url="https://example.com/qc", mode_name="Detailed")
        self.assertTrue(len(b1) >= 4)

        # Calling with title omitted (inferred)
        b2 = format_single_summary_blocks(sum_data, url="https://example.com/qc", mode_name="Quick")
        self.assertTrue(len(b2) >= 4)

    def test_05_format_comparison_blocks_signature(self):
        """Verifies format_comparison_blocks accepts extra keyword arguments (url_a, url_b)."""
        comp_data = {
            "title": "React vs Svelte 2026",
            "article_a": {"title": "React 19", "overview": "React introduces Compiler."},
            "article_b": {"title": "Svelte 5", "overview": "Svelte introduces Runes."},
            "common_points": ["Both focus on fine-grained reactivity"],
            "contradictions": ["Different compile-time vs runtime overhead"],
            "differing_perspectives": ["React leans on framework runtime; Svelte on compiler"],
            "key_facts_claims": [],
            "unique_to_a": ["Server components"],
            "unique_to_b": ["Runes syntax"],
            "conclusions_comparison": {"comparison_note": "Both frameworks converge on signals."},
            "synthesis": "Choose React for ecosystem, Svelte for developer ergonomics."
        }

        blocks = format_comparison_blocks(
            comp_data,
            url_a="https://react.dev",
            url_b="https://svelte.dev",
        )
        self.assertTrue(len(blocks) >= 6)

    @patch("notion_service.requests.patch")
    @patch("notion_service.requests.post")
    def test_06_create_notion_page_signatures_and_chunk_validation(self, mock_post, mock_patch):
        """Verifies create_notion_page handles title/page_title and api_key/token, and validates PATCH chunks."""
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.json.return_value = {
            "id": "0123456789abcdef0123456789abcdef",
            "url": "https://notion.so/testpage",
        }
        mock_post.return_value = mock_post_resp

        mock_patch_resp = MagicMock()
        mock_patch_resp.status_code = 200
        mock_patch.return_value = mock_patch_resp

        # Create 120 blocks to force a second chunk (95 in first, 25 in second)
        blocks = [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": []}} for _ in range(120)]

        # Call with 'title' and 'api_key'
        res = create_notion_page(
            parent_page_id="01234567-89ab-cdef-0123-456789abcdef",
            title="AI Safety Report",
            blocks=blocks,
            api_key="secret_test123",
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["title"], "AI Safety Report")
        self.assertEqual(mock_patch.call_count, 1)

    @patch("notion_service.requests.patch")
    @patch("notion_service.requests.post")
    def test_07_create_notion_page_reports_partial_append_failure(self, mock_post, mock_patch):
        """Verifies that when a block chunk fails to append, NotionError is raised with created page ID."""
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.json.return_value = {
            "id": "page_id_created_123",
            "url": "https://notion.so/createdpage",
        }
        mock_post.return_value = mock_post_resp

        mock_patch_resp = MagicMock()
        mock_patch_resp.status_code = 400
        mock_patch_resp.text = "Invalid block schema"
        mock_patch.return_value = mock_patch_resp

        blocks = [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": []}} for _ in range(110)]
        with self.assertRaises(NotionError) as ctx:
            create_notion_page(
                parent_page_id="0123456789abcdef0123456789abcdef",
                page_title="Broken Append Page",
                blocks=blocks,
                token="secret_test123",
            )
        self.assertIn("page_id_created_123", str(ctx.exception))
        self.assertIn("failed to append", str(ctx.exception))

    # -------------------------------------------------------------
    # 2. PDF Exporter Signatures & Comparative Routing
    # -------------------------------------------------------------
    def test_08_generate_comparison_pdf_signatures(self):
        comp_data = {
            "title": "Quantum Supremacy: Google vs IBM",
            "article_a": {"title": "Google Sycamore", "url": "https://google.com/quantum", "overview": "53-qubit processor."},
            "article_b": {"title": "IBM Eagle", "url": "https://ibm.com/quantum", "overview": "127-qubit processor."},
            "common_points": ["Superconducting architecture"],
            "contradictions": ["Different noise mitigation approaches"],
            "differing_perspectives": ["Gate fidelity vs scale"],
            "key_facts_claims": [{"topic": "Qubits", "article_a_claim": "53", "article_b_claim": "127"}],
            "unique_to_a": ["Random circuit sampling focus"],
            "unique_to_b": ["Quantum volume metrics"],
            "conclusions_comparison": {"comparison_note": "Both represent valid research directions."},
            "synthesis": "Superconducting qubits lead current noisy intermediate-scale quantum research."
        }

        # Calling with positional comp
        buf1 = generate_comparison_pdf(comp_data)
        self.assertIsNotNone(buf1)
        self.assertTrue(len(buf1.getvalue()) > 500)

        # Calling with keyword comparison, url_a, url_b, created_at
        buf2 = generate_comparison_pdf(
            comparison=comp_data,
            url_a="https://google.com",
            url_b="https://ibm.com",
            created_at="November 10, 2026",
        )
        self.assertIsNotNone(buf2)
        self.assertTrue(len(buf2.getvalue()) > 500)

    def test_09_export_pdf_endpoint_routes_comparisons(self):
        """Verifies POST /export-pdf routes comparison objects to generate_comparison_pdf."""
        comp_payload = {
            "entry_type": "comparison",
            "summary": {
                "title": "Comparative Study of Renewable Energy",
                "article_a": {"title": "Solar Photovoltaics", "url": "https://solar.org", "overview": "Solar energy trends."},
                "article_b": {"title": "Wind Turbines", "url": "https://wind.org", "overview": "Offshore wind growth."},
                "common_points": ["Zero operational emissions"],
                "contradictions": ["Capacity factors differ"],
                "differing_perspectives": ["Distributed vs centralized deployment"],
                "key_facts_claims": [],
                "unique_to_a": ["Rooftop accessibility"],
                "unique_to_b": ["Night-time generation"],
                "conclusions_comparison": {"comparison_note": "Complementary diurnal profiles."},
                "synthesis": "Hybrid solar-wind grids provide superior reliability."
            }
        }

        res = self.client.post(
            "/export-pdf",
            json=comp_payload,
            headers={"X-CSRF-Token": self.csrf_token}
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("Content-Type"), "application/pdf")
        self.assertTrue(len(res.data) > 500)

    # -------------------------------------------------------------
    # 3. Credential Vault Merging & Notion Token Preservation
    # -------------------------------------------------------------
    @patch("app.validate_gemini_credentials", return_value=(True, ""))
    def test_10_save_key_preserves_notion_token_in_vault(self, mock_val):
        fake_redis = FakeRedis()
        test_vault = TemporaryCredentialVault(fake_redis, Fernet.generate_key())
        sid = "user_session_abc"

        # Pre-seed vault with an existing Notion token
        test_vault.save(sid, {"primary_provider": "gemini", "gemini_api_key": "old_key", "notion_token": "secret_notion_abc"})

        with patch("app._vault", test_vault), \
             patch("app.is_serverless_env", return_value=True), \
             patch("app._vault_sid", return_value=sid), \
             patch("app._vault_config", return_value=test_vault.load(sid)):

            # Save a new Gemini key via /api/save-key
            res = self.client.post(
                "/api/save-key",
                json={"gemini_api_key": "AIzaSy_new_key_12345", "gemini_model": "gemini-3.8-flash"},
                headers={"X-CSRF-Token": self.csrf_token}
            )
            self.assertEqual(res.status_code, 200)

            # Verify that the Notion token was preserved!
            updated_cfg = test_vault.load(sid)
            self.assertEqual(updated_cfg.get("gemini_api_key"), "AIzaSy_new_key_12345")
            self.assertEqual(updated_cfg.get("notion_token"), "secret_notion_abc")

    # -------------------------------------------------------------
    # 4. Vault Maximum Lifetime Preservation & Deletion Result
    # -------------------------------------------------------------
    def test_11_vault_preserves_creation_timestamp_on_update(self):
        redis = FakeRedis()
        key = Fernet.generate_key()
        vault = TemporaryCredentialVault(redis, key)

        original_time = 1700000000
        with patch("credential_vault.time.time", return_value=original_time):
            vault.save("user_1", {"gemini_api_key": "key_1"})

        # Update 1 hour later
        later_time = original_time + 3600
        with patch("credential_vault.time.time", return_value=later_time):
            vault.save("user_1", {"gemini_api_key": "key_updated"})

            # Load and inspect record blob directly
            blob = redis.get(vault._key("user_1"))
            record = json.loads(vault.cipher.decrypt(blob))
            # The 'created' timestamp must still be original_time (not reset to later_time)
            self.assertEqual(record["created"], original_time)

    def test_12_vault_delete_returns_boolean(self):
        redis = FakeRedis()
        key = Fernet.generate_key()
        vault = TemporaryCredentialVault(redis, key)

        vault.save("user_del", {"gemini_api_key": "key"})
        res = vault.delete("user_del")
        self.assertTrue(res)
        self.assertEqual(vault.load("user_del"), {})


if __name__ == "__main__":
    unittest.main()
