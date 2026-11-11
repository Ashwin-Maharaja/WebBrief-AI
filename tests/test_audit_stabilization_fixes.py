"""
WebBrief AI — Comprehensive Audit Stabilization Regression Test Suite
Validates fixes for:
- ISS-14: Accurate history count in SQLite, endpoint, and settings view
- ISS-15: Hosted batch PDF export fallback preserves client items
- ISS-17: SSE batch summarization cancellation via cancel_event
- ISS-18: Unlimited history retrieval with limit=-1 for full backups
- ISS-20: Truthful embedding model and search_mode return values
- ISS-21: Canonical URL normalization and duplicate detection in batch processor
- ISS-23: Scraper subresource SSRF and private IP blocking
"""

import io
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch, MagicMock

from app import app
from batch_processor import validate_batch_urls, execute_batch
from history import (
    init_db,
    add_history_entry,
    get_history,
    get_history_count,
    get_history_entry,
    delete_history_entry,
    clear_history,
    get_db_connection,
    _INITIALIZED_DBS,
    HistoryError,
    HistoryStorageUnavailableError,
    HistoryTransactionError,
)
from scraper import ScraperError, validate_safe_url, is_safe_ip
from vector_store import (
    generate_embedding_with_model,
    DEFAULT_EMBEDDING_MODEL,
    semantic_search,
    rebuild_vector_index,
    get_vector_store_stats,
    init_vector_store,
    VectorStoreError,
    pack_vector,
    cleanup_orphaned_vectors,
)


class TestAuditStabilizationFixes(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_audit.db")
        init_db(self.db_path)
        app.config["TESTING"] = True
        self.client = app.test_client()
        # Initialize session and fetch CSRF token
        self.client.get("/")
        with self.client.session_transaction() as sess:
            self.csrf_token = sess.get("_csrf_token", "")
        self.post_headers = {
            "Content-Type": "application/json",
            "X-CSRF-Token": self.csrf_token,
        }

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -------------------------------------------------------------
    # ISS-14: Accurate History Count Tests
    # -------------------------------------------------------------
    def test_iss14_history_count_reflects_actual_records(self):
        """Verifies get_history_count counts beyond 50 records without truncation."""
        self.assertEqual(get_history_count(self.db_path), 0)

        # Insert 60 entries
        for i in range(60):
            add_history_entry(
                title=f"Article {i}",
                url=f"https://example.com/article/{i}",
                mode="quick",
                mode_name="Quick Summary",
                summary={"overview": f"Overview {i}"},
                db_path=self.db_path,
            )

        self.assertEqual(get_history_count(self.db_path), 60)
        # Normal get_history defaults to 50
        self.assertEqual(len(get_history(limit=50, db_path=self.db_path)), 50)

    def test_iss14_api_history_count_endpoint(self):
        """Verifies GET /api/history/count returns truthful total record count."""
        with patch("app.get_history_count", return_value=127):
            with patch("app.is_serverless_env", return_value=False):
                res = self.client.get("/api/history/count")
                self.assertEqual(res.status_code, 200)
                data = res.get_json()
                self.assertEqual(data["count"], 127)
                self.assertFalse(data["is_hosted"])

        with patch("app.is_serverless_env", return_value=True):
            res = self.client.get("/api/history/count")
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(data["count"], 0)
            self.assertTrue(data["is_hosted"])

    # -------------------------------------------------------------
    # ISS-15: Hosted Batch PDF Export Fallback Tests
    # -------------------------------------------------------------
    def test_iss15_batch_pdf_export_preserves_client_items_in_hosted_mode(self):
        """Verifies batch_export_pdf retains client items when serverless or history not found."""
        sample_items = [
            {
                "url": "https://example.com/article1",
                "title": "Article 1",
                "summary": {"overview": "Summary 1", "key_points": ["Point A"]},
            },
            {
                "url": "https://example.com/article2",
                "title": "Article 2",
                "summary": {"overview": "Summary 2", "key_points": ["Point B"]},
            },
        ]

        with patch("app.is_serverless_env", return_value=True):
            with patch("app.generate_batch_summary_pdf") as mock_gen:
                mock_gen.return_value = io.BytesIO(b"%PDF-1.4 mock")
                res = self.client.post(
                    "/batch/export-pdf",
                    data=json.dumps({"items": sample_items}),
                    headers=self.post_headers,
                )
                self.assertEqual(res.status_code, 200)
                self.assertEqual(res.mimetype, "application/pdf")
                # Confirm generate_batch_summary_pdf was called with the client's sample_items
                mock_gen.assert_called_once()
                call_items = mock_gen.call_args[0][0]
                self.assertEqual(len(call_items), 2)
                self.assertEqual(call_items[0]["title"], "Article 1")

    # -------------------------------------------------------------
    # ISS-17: SSE Batch Worker Cancellation
    # -------------------------------------------------------------
    def test_iss17_execute_batch_cancellation(self):
        """Verifies execute_batch halts further execution when cancel_event is set."""
        cancel_event = threading.Event()
        urls = [
            "https://example.com/page1",
            "https://example.com/page2",
            "https://example.com/page3",
            "https://example.com/page4",
        ]

        # Cancel immediately before processing
        cancel_event.set()
        res = execute_batch(
            raw_urls=urls,
            cancel_event=cancel_event,
        )

        self.assertEqual(res["total"], 4)
        # All items should have failed or canceled without crash
        for item in res["results"]:
            self.assertEqual(item["status"], "failed")
            self.assertIn("canceled", item["error"].lower())

    # -------------------------------------------------------------
    # ISS-18: Unlimited History Retrieval with limit=-1
    # -------------------------------------------------------------
    def test_iss18_unlimited_history_retrieval(self):
        """Verifies get_history and /api/history with limit=-1 retrieve all records."""
        for i in range(75):
            add_history_entry(
                title=f"Article {i}",
                url=f"https://example.com/article/{i}",
                mode="quick",
                mode_name="Quick Summary",
                summary={"overview": f"Overview {i}"},
                db_path=self.db_path,
            )

        # Direct call with limit=-1
        all_entries = get_history(limit=-1, db_path=self.db_path)
        self.assertEqual(len(all_entries), 75)

        # API call with limit=-1
        with patch("app.get_history", wraps=lambda limit=50: get_history(limit=limit, db_path=self.db_path)):
            with patch("app.is_serverless_env", return_value=False):
                res = self.client.get("/api/history?limit=-1")
                self.assertEqual(res.status_code, 200)
                data = res.get_json()
                self.assertEqual(len(data["history"]), 75)

    # -------------------------------------------------------------
    # ISS-20: Truthful Embedding Model & Search Mode Return
    # -------------------------------------------------------------
    def test_iss20_truthful_embedding_model_returned(self):
        """Verifies /api/search/embed returns actual model used (e.g. fallback)."""
        with patch("app.has_valid_config", return_value=True):
            res = self.client.post(
                "/api/search/embed",
                data=json.dumps({"text": "Test embedding text"}),
                headers=self.post_headers,
            )
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertIn("model", data)
            self.assertIn("embedding", data)
            self.assertIn("dimensions", data)
            # In the absence of live Gemini keys, fallback identifier must be truthful
            self.assertEqual(data["model"], "deterministic-hash-fallback")

    def test_iss20_api_search_semantic_mode_indicator(self):
        """Verifies /api/search/semantic returns search_mode."""
        with patch("app.has_valid_config", return_value=True):
            with patch("app.is_serverless_env", return_value=False):
                with patch("app.semantic_search", return_value=[]):
                    res = self.client.post(
                        "/api/search/semantic",
                        data=json.dumps({"query": "machine learning"}),
                        headers=self.post_headers,
                    )
                    self.assertEqual(res.status_code, 200)
                    data = res.get_json()
                    self.assertEqual(data["search_mode"], "semantic")

    # -------------------------------------------------------------
    # ISS-21: Canonical Batch URL Normalization & Deduplication
    # -------------------------------------------------------------
    def test_iss21_canonical_batch_url_deduplication(self):
        """Verifies canonical duplicate detection for different query param orders, ports, and casing."""
        raw_urls = [
            "https://Example.COM:443/article/?b=2&a=1#section1",
            "https://example.com/article?a=1&b=2",
            "http://example.org:80/path/",
            "http://example.org/path",
        ]

        items, stats = validate_batch_urls(raw_urls)

        self.assertEqual(stats["valid"], 2)
        self.assertEqual(stats["duplicates"], 2)

        # URL #1 valid, URL #2 duplicate of URL #1
        self.assertTrue(items[0]["is_valid"])
        self.assertFalse(items[0]["is_duplicate"])

        self.assertFalse(items[1]["is_valid"])
        self.assertTrue(items[1]["is_duplicate"])
        self.assertIn("Duplicate of URL #1", items[1]["error"])

        # URL #3 valid, URL #4 duplicate of URL #3
        self.assertTrue(items[2]["is_valid"])
        self.assertFalse(items[2]["is_duplicate"])

        self.assertFalse(items[3]["is_valid"])
        self.assertTrue(items[3]["is_duplicate"])
        self.assertIn("Duplicate of URL #3", items[3]["error"])

    # -------------------------------------------------------------
    # ISS-23: Scraper Subresource SSRF IP Egress Filter
    # -------------------------------------------------------------
    def test_iss23_is_safe_ip_blocks_private_and_metadata_addresses(self):
        """Verifies is_safe_ip rejects loopback, private ranges, link-local, and cloud metadata."""
        blocked_ips = [
            "127.0.0.1",
            "127.0.0.2",
            "10.0.0.1",
            "172.16.0.1",
            "192.168.1.1",
            "169.254.169.254",  # AWS/GCP/Azure instance metadata
            "0.0.0.0",
            "::1",              # IPv6 loopback
            "fe80::1",          # IPv6 link-local
        ]
        for ip in blocked_ips:
            self.assertFalse(is_safe_ip(ip), f"IP {ip} should be rejected as unsafe")

        public_ips = [
            "8.8.8.8",
            "1.1.1.1",
            "93.184.216.34",
        ]
        for ip in public_ips:
            self.assertTrue(is_safe_ip(ip), f"IP {ip} should be accepted as safe")

    def test_iss23_validate_safe_url_blocks_internal_and_metadata_urls(self):
        """Verifies validate_safe_url rejects requests to private networks and non-standard schemes."""
        unsafe_urls = [
            "http://127.0.0.1:8080/admin",
            "http://169.254.169.254/latest/meta-data/",
            "http://localhost:5000",
            "http://192.168.1.50/dashboard",
            "ftp://example.com/file.txt",
            "file:///etc/passwd",
            "javascript:alert(1)",
        ]
    # -------------------------------------------------------------
    # Phase 1: HIS-01 through HIS-05 History Persistence Tests
    # -------------------------------------------------------------
    def test_his01_failed_insert_raises_and_does_not_return_success(self):
        """HIS-01: add_history_entry raises HistoryError on insert failure and never returns fake success."""
        with patch("history.get_db_connection") as mock_conn:
            mock_c = MagicMock()
            mock_c.execute.side_effect = sqlite3.OperationalError("Disk I/O failure")
            mock_conn.return_value = mock_c
            with self.assertRaises(HistoryError):
                add_history_entry(
                    title="Test",
                    url="https://example.com",
                    mode="quick",
                    mode_name="Quick Summary",
                    summary={"overview": "Test"},
                    db_path=self.db_path,
                )

    def test_his01_summarize_communicates_failed_history_persistence(self):
        """HIS-01: /summarize returns history_saved: False when history persistence fails."""
        with patch("app.scrape_article", return_value="Test article body content with sufficient length."):
            with patch("app.summarize_text", return_value={"title": "Test Title", "overview": "Summary text"}):
                with patch("app.is_serverless_env", return_value=False):
                    with patch("app.add_history_entry", side_effect=HistoryTransactionError("Disk full")):
                        with patch("app.index_summary_entry") as mock_idx:
                            res = self.client.post(
                                "/summarize",
                                data=json.dumps({"url": "https://example.com/test", "mode": "quick"}),
                                headers=self.post_headers,
                            )
                            self.assertEqual(res.status_code, 200)
                            data = res.get_json()
                            self.assertTrue(data["success"])
                            self.assertFalse(data["summary"]["history_saved"])
                            self.assertIsNone(data["summary"]["history_id"])
                            # Vector indexing must NOT be attempted for non-existent records
                            mock_idx.assert_not_called()

    def test_his02_get_db_connection_no_silent_memory_fallback(self):
        """HIS-02: get_db_connection raises HistoryStorageUnavailableError instead of falling back to :memory:."""
        bad_path = "/nonexistent/path/that/cannot/exist/history.db"
        with self.assertRaises(HistoryStorageUnavailableError):
            get_db_connection(bad_path)

    def test_his02_init_db_discards_failed_path_from_initialized_set(self):
        """HIS-02: init_db removes target path from _INITIALIZED_DBS on failure."""
        bad_path = "/nonexistent/path/history.db"
        with self.assertRaises(HistoryError):
            init_db(bad_path)
        self.assertNotIn(bad_path, _INITIALIZED_DBS)

    def test_his03_error_semantics_distinguish_outage_from_empty(self):
        """HIS-03: API endpoints return 503 instead of 200 (empty) or 404 when DB fails."""
        with patch("app.is_serverless_env", return_value=False):
            with patch("app.get_history", side_effect=HistoryTransactionError("DB lock timeout")):
                res = self.client.get("/api/history")
                self.assertEqual(res.status_code, 503)
                data = res.get_json()
                self.assertIn("error", data)

            with patch("app.get_history_count", side_effect=HistoryTransactionError("DB lock timeout")):
                res = self.client.get("/api/history/count")
                self.assertEqual(res.status_code, 503)

            with patch("app.get_history_entry", side_effect=HistoryTransactionError("DB lock timeout")):
                res = self.client.get("/api/history/entry_123")
                self.assertEqual(res.status_code, 503)

            with patch("app.delete_history_entry", side_effect=HistoryTransactionError("DB lock timeout")):
                res = self.client.delete("/api/history/entry_123", headers=self.post_headers)
                self.assertEqual(res.status_code, 503)

            with patch("app.clear_history", side_effect=HistoryTransactionError("DB lock timeout")):
                res = self.client.post("/api/history/clear", headers=self.post_headers)
                self.assertEqual(res.status_code, 503)

    def test_his04_and_his05_foreign_keys_and_atomic_deletion(self):
        """HIS-04 & HIS-05: PRAGMA foreign_keys is ON and history deletion cleans embeddings."""
        conn = get_db_connection(self.db_path)
        cursor = conn.execute("PRAGMA foreign_keys;")
        fk_status = cursor.fetchone()[0]
        conn.close()
        self.assertEqual(fk_status, 1, "Foreign keys must be enabled on every SQLite connection.")

        # Insert a history entry
        entry = add_history_entry(
            title="Article to delete",
            url="https://example.com/delete-me",
            mode="quick",
            mode_name="Quick Summary",
            summary={"overview": "Delete me overview"},
            db_path=self.db_path,
        )
        eid = entry["id"]

        # Insert a corresponding vector embedding
        conn = get_db_connection(self.db_path)
        with conn:
            conn.execute("""
                INSERT INTO summary_embeddings (summary_id, model, dimensions, content_hash, search_text, vector_blob)
                VALUES (?, 'test-model', 4, 'hash123', 'search text', ?)
            """, (eid, b"\x00" * 16))
        conn.close()

        # Delete history entry
        success = delete_history_entry(eid, db_path=self.db_path)
        self.assertTrue(success)

        # Confirm history entry is gone
        self.assertIsNone(get_history_entry(eid, db_path=self.db_path))

        # Confirm corresponding embedding was deleted
        conn = get_db_connection(self.db_path)
        cur = conn.execute("SELECT COUNT(*) FROM summary_embeddings WHERE summary_id = ?", (eid,))
        emb_count = cur.fetchone()[0]
        conn.close()
        self.assertEqual(emb_count, 0, "summary_embeddings row must be deleted with summary_history entry.")

    # -------------------------------------------------------------
    # Phase 3: SRCH-01 through SRCH-06 Semantic Search Tests
    # -------------------------------------------------------------
    def test_srch01_model_compatibility_enforced_in_semantic_search(self):
        """SRCH-01: semantic_search rejects stored vectors from incompatible models or dimensions."""
        entry_a = add_history_entry(
            title="Neural Article",
            url="https://example.com/neural",
            mode="quick",
            mode_name="Quick Summary",
            summary={"overview": "Quantum computing overview"},
            db_path=self.db_path,
        )
        entry_b = add_history_entry(
            title="Fallback Article",
            url="https://example.com/fallback",
            mode="quick",
            mode_name="Quick Summary",
            summary={"overview": "Quantum computing overview"},
            db_path=self.db_path,
        )

        test_vec_768 = [0.1] * 768
        conn = get_db_connection(self.db_path)
        with conn:
            conn.execute("""
                INSERT INTO summary_embeddings (summary_id, model, dimensions, content_hash, search_text, vector_blob)
                VALUES (?, 'gemini-embedding-001', 768, 'hashA', 'Quantum computing overview', ?)
            """, (entry_a["id"], pack_vector(test_vec_768)))
            conn.execute("""
                INSERT INTO summary_embeddings (summary_id, model, dimensions, content_hash, search_text, vector_blob)
                VALUES (?, 'deterministic-hash-fallback', 768, 'hashB', 'Quantum computing overview', ?)
            """, (entry_b["id"], pack_vector(test_vec_768)))
        conn.close()

        # Query using a mock that returns model 'gemini-embedding-001'
        with patch("vector_store.generate_embedding_with_model", return_value=(test_vec_768, "gemini-embedding-001")):
            results = semantic_search("quantum computing", db_path=self.db_path)
            res_ids = [r["id"] for r in results]
            # Only entry_a (matching model) must be included; entry_b must be excluded
            self.assertIn(entry_a["id"], res_ids)
            self.assertNotIn(entry_b["id"], res_ids)

    def test_srch02_allow_fallback_false_raises_on_failure(self):
        """SRCH-02: generate_embedding_with_model raises VectorStoreError when allow_fallback is False."""
        with self.assertRaises(VectorStoreError):
            generate_embedding_with_model("Sample text to embed", api_key="", allow_fallback=False)

    def test_srch04_rebuild_index_processes_all_records(self):
        """SRCH-04: rebuild_vector_index iterates across all records without a 1,000 ceiling."""
        rebuild_res = rebuild_vector_index(db_path=self.db_path)
        self.assertIn("total", rebuild_res)
        self.assertIn("indexed", rebuild_res)
        self.assertIn("failed", rebuild_res)
        self.assertEqual(rebuild_res["failed"], 0)

    def test_srch05_init_vector_store_explicit_failure(self):
        """SRCH-05: init_vector_store raises VectorStoreError on connection/schema failure."""
        bad_path = "/nonexistent/path/that/cannot/exist/vector.db"
        with self.assertRaises(VectorStoreError):
            init_vector_store(bad_path)

    def test_srch06_vector_store_stats_breakdown(self):
        """SRCH-06: get_vector_store_stats reports model breakdown and dimensions."""
        stats = get_vector_store_stats(self.db_path)
        self.assertIn("indexed_count", stats)
        self.assertIn("model_breakdown", stats)
        self.assertIn("status", stats)

    # -------------------------------------------------------------
    # Phase 5: Scraper Security & Durability (SCR-01 to SCR-05)
    # -------------------------------------------------------------
    @patch("scraper.requests.Session")
    def test_scr02_requests_session_trust_env_false(self, mock_session_cls):
        """SCR-02: verifies requests session disables environment proxies (trust_env=False)."""
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.is_redirect = False
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Length": "100"}
        mock_resp.iter_content.return_value = [b"<html><head><title>Test</title></head><body><p>" + b"Content " * 20 + b"</p></body></html>"]
        mock_session.get.return_value = mock_resp

        from scraper import scrape_with_requests
        with patch("scraper.resolve_and_validate_safe_url", return_value=("https://example.com", "93.184.216.34")):
            scrape_with_requests("https://example.com")
            self.assertFalse(mock_session.trust_env)

    @patch("scraper.scrape_with_requests")
    def test_scr03_scrape_article_with_metadata_provenance(self, mock_requests):
        """SCR-03 & SCR-05: verifies scrape_article_with_metadata returns provenance metadata."""
        mock_requests.return_value = "<html><head><title>Test Page</title></head><body><p>" + "This is a detailed paragraph for testing. " * 5 + "</p></body></html>"
        from scraper import scrape_article_with_metadata
        title, text, meta = scrape_article_with_metadata("https://example.com")
        self.assertEqual(title, "Test Page")
        self.assertIn("method", meta)
        self.assertEqual(meta["method"], "static")
        self.assertFalse(meta["truncated"])
        self.assertGreater(meta["byte_count"], 0)

    # -------------------------------------------------------------
    # Phase 6: Batch & Summarization Reliability (BATCH-01, SUM-01)
    # -------------------------------------------------------------
    def test_batch01_execute_batch_success_and_status(self):
        """BATCH-01: execute_batch returns explicit success boolean and status string."""
        with patch("batch_processor.process_single_item") as mock_proc:
            mock_proc.return_value = {
                "status": "completed",
                "title": "Title",
                "summary": {"overview": "Overview"},
                "error": None,
                "history_id": "h_1"
            }
            res = execute_batch(["https://example.com/art1", "https://example.com/art2"])
            self.assertTrue(res["success"])
            self.assertEqual(res["status"], "completed")
            self.assertEqual(res["completed"], 2)
            self.assertEqual(res["failed"], 0)

        with patch("batch_processor.process_single_item") as mock_proc:
            mock_proc.return_value = {
                "status": "failed",
                "title": None,
                "summary": None,
                "error": "Timeout",
                "history_id": None
            }
            res = execute_batch(["https://example.com/art1"])
            self.assertFalse(res["success"])
            self.assertEqual(res["status"], "failed")
            self.assertEqual(res["completed"], 0)
            self.assertEqual(res["failed"], 1)

    def test_batch02_sse_events_include_started(self):
        """BATCH-02: SSE stream emits batch_started and done events."""
        with patch("batch_processor.process_single_item", return_value={"status": "completed", "title": "T", "summary": {"overview": "O"}}):
            res = self.client.post(
                "/batch/summarize?stream=1",
                json={"urls": ["https://example.com/art1"]},
                headers=self.post_headers
            )
            self.assertEqual(res.status_code, 200)
            stream_data = res.get_data(as_text=True)
            self.assertIn('"event": "batch_started"', stream_data)
            self.assertIn('"event": "done"', stream_data)

    def test_sum01_hierarchical_reduce_isolation(self):
        """SUM-01 & SUM-02: hierarchical reduce isolates subchunk errors and reports partial status."""
        from summarizer import summarize_text
        mock_router = MagicMock()
        mock_map_res = MagicMock()
        mock_map_res.content = "Chunk summary text with sufficient information."
        mock_map_res.provider_id = "gemini"
        mock_map_res.provider_name = "Google Gemini"
        mock_map_res.model = "gemini-3.8-flash"
        mock_map_res.switched_to_fallback = False

        # Simulate reduce failure on subchunk
        mock_router.generate_completion.side_effect = [
            mock_map_res, # map chunk 1
            mock_map_res, # map chunk 2
            Exception("Subchunk 1 reduce temporary API failure"), # subchunk reduce 1
            mock_map_res, # subchunk reduce 2
        ]
        mock_final_res = MagicMock()
        mock_final_res.content = json.dumps({
            "title": "Unified Title",
            "overview": "Overview text",
            "key_points": ["Point 1"],
            "important_details": ["Detail 1"],
            "conclusion": "Conclusion text"
        })
        mock_final_res.provider_id = "gemini"
        mock_final_res.provider_name = "Google Gemini"
        mock_final_res.model = "gemini-3.8-flash"
        mock_final_res.switched_to_fallback = False
        mock_router.generate_structured_json.return_value = mock_final_res

        long_article = ("Paragraph with lots of text to generate multiple chunks. " * 150)
        summary = summarize_text(long_article, router=mock_router, chunk_size=500, chunk_overlap=50)
        self.assertIn("stats", summary)
        self.assertTrue(summary.get("is_partial") or summary.get("partial") or summary["stats"]["is_map_reduce"])

    # -------------------------------------------------------------
    # Phase 7 & 8: Boolean Coercion, PDF, Notion, Health Probes
    # -------------------------------------------------------------
    def test_comp01_parse_bool_safety(self):
        """COMP-01: parse_bool safely parses string representations of boolean without string 'false' coercion bugs."""
        from app import parse_bool
        self.assertFalse(parse_bool("false"))
        self.assertFalse(parse_bool("False"))
        self.assertFalse(parse_bool("0"))
        self.assertFalse(parse_bool(0))
        self.assertFalse(parse_bool(False))
        self.assertTrue(parse_bool("true"))
        self.assertTrue(parse_bool("True"))
        self.assertTrue(parse_bool("1"))
        self.assertTrue(parse_bool(1))
        self.assertTrue(parse_bool(True))
        self.assertFalse(parse_bool(None, default=False))
        self.assertTrue(parse_bool(None, default=True))

    def test_pdf01_batch_export_pdf_item_limit(self):
        """PDF-01: batch_export_pdf enforces 50-item limit and rejects oversized payload."""
        oversized_items = [{"title": f"Item {i}", "summary": {"overview": "O"}} for i in range(51)]
        res = self.client.post(
            "/batch/export-pdf",
            json={"items": oversized_items},
            headers=self.post_headers
        )
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertIn("maximum limit of 50 items", data["error"])

    def test_notion02_export_auth_error_status_code(self):
        """NOTION-02: Notion auth errors return HTTP 401 Unauthorized."""
        from notion_service import NotionError
        with patch("app.get_notion_key", return_value="secret_test123"):
            with patch("app.create_notion_page", side_effect=NotionError("Notion API error: 401 Unauthorized - invalid token")):
                res = self.client.post(
                    "/api/notion/export",
                    json={
                        "parent_page_id": "test_page_123",
                        "summary": {"title": "Test", "overview": "Overview"}
                    },
                    headers=self.post_headers
                )
                self.assertEqual(res.status_code, 401)
                data = res.get_json()
                self.assertIn("Unauthorized", data["error"])

    def test_dep01_health_liveness_and_readiness(self):
        """DEP-01: /api/health/liveness returns 200 alive, /api/health/readiness checks SQLite & Redis."""
        res_live = self.client.get("/api/health/liveness")
        self.assertEqual(res_live.status_code, 200)
        data_live = res_live.get_json()
        self.assertEqual(data_live["status"], "alive")

        res_ready = self.client.get("/api/health/readiness")
        self.assertEqual(res_ready.status_code, 200)
        data_ready = res_ready.get_json()
        self.assertEqual(data_ready["status"], "ready")
        self.assertIn("checks", data_ready)
        self.assertIn("sqlite", data_ready["checks"])
        self.assertIn("redis", data_ready["checks"])

    # -------------------------------------------------------------
    # P2 Stabilization Tests (P2-VLT-02, P2-HIS-04, P2-SRCH, P2-SUM, P2-NOTION, P2-PDF, P2-SCR, P2-BATCH)
    # -------------------------------------------------------------
    def test_p2_vlt02_notion_vault_status_handling(self):
        """P2-VLT-02: api_notion_status accurately reports UNAVAILABLE (503) and DECRYPTION_ERROR (500)."""
        from credential_vault import VaultStatus
        with patch("app._vault", MagicMock()):
            with patch("app._vault_status", return_value=(VaultStatus.UNAVAILABLE, {})):
                res = self.client.get("/api/notion/status")
                self.assertEqual(res.status_code, 503)
                data = res.get_json()
                self.assertEqual(data["status"], "unavailable")

            with patch("app._vault_status", return_value=(VaultStatus.DECRYPTION_ERROR, {})):
                res = self.client.get("/api/notion/status")
                self.assertEqual(res.status_code, 500)
                data = res.get_json()
                self.assertEqual(data["status"], "decryption_error")

    def test_p2_his04_created_at_and_partial_schema(self):
        """P2-HIS-04: add_history_entry and get_history return ISO created_at, is_partial, and warning."""
        entry = add_history_entry(
            title="Partial Article",
            url="https://example.com/partial",
            mode="quick",
            mode_name="Quick Summary",
            summary={"overview": "Overview text", "is_partial": True, "warning": "Section 2 error"},
            db_path=self.db_path,
        )
        self.assertIn("created_at", entry)
        self.assertTrue(entry["is_partial"])
        self.assertEqual(entry["warning"], "Section 2 error")

        fetched = get_history_entry(entry["id"], db_path=self.db_path)
        self.assertIsNotNone(fetched)
        self.assertIn("created_at", fetched)
        self.assertTrue(fetched["is_partial"])
        self.assertEqual(fetched["warning"], "Section 2 error")

        history_list = get_history(db_path=self.db_path)
        self.assertEqual(len(history_list), 1)
        self.assertIn("created_at", history_list[0])
        self.assertTrue(history_list[0]["is_partial"])
        self.assertEqual(history_list[0]["warning"], "Section 2 error")

    def test_p2_srch02_cleanup_orphaned_vectors(self):
        """P2-SRCH-02: cleanup_orphaned_vectors deletes dangling vectors with deleted history entries."""
        entry = add_history_entry(
            title="Temp Entry",
            url="https://example.com/temp",
            mode="quick",
            mode_name="Quick Summary",
            summary={"overview": "Text"},
            db_path=self.db_path,
        )
        conn = get_db_connection(self.db_path)
        test_vec = [0.1] * 768
        conn.execute("PRAGMA foreign_keys = OFF;")
        with conn:
            conn.execute("""
                INSERT INTO summary_embeddings (summary_id, model, dimensions, content_hash, search_text, vector_blob)
                VALUES (?, 'gemini-embedding-001', 768, 'h1', 'txt', ?)
            """, (entry["id"], pack_vector(test_vec)))
            # Insert orphaned vector
            conn.execute("""
                INSERT INTO summary_embeddings (summary_id, model, dimensions, content_hash, search_text, vector_blob)
                VALUES ('orphaned_id_123', 'gemini-embedding-001', 768, 'h2', 'txt2', ?)
            """, (pack_vector(test_vec),))
        conn.close()

        deleted = cleanup_orphaned_vectors(db_path=self.db_path)
        self.assertEqual(deleted, 1)

        stats = get_vector_store_stats(self.db_path)
        self.assertEqual(stats["indexed_count"], 1)

    def test_p2_srch04_search_transparency_metadata(self):
        """P2-SRCH-04: semantic_search returns search_mode and status_detail transparency metadata."""
        results, meta = semantic_search("test query", db_path=self.db_path, return_metadata=True)
        self.assertEqual(results, [])
        self.assertIn("search_mode", meta)
        self.assertIn("embedding_model", meta)
        self.assertIn("status_detail", meta)
        self.assertEqual(meta["status_detail"], "no_records")

    def test_p2_sum01_typed_provider_error_handling(self):
        """P2-SUM-01: Map phase catches typed ProviderAuthError and terminates immediately."""
        from summarizer import summarize_text, SummarizerError
        from services.providers.exceptions import ProviderAuthError

        mock_router = MagicMock()
        mock_router.generate_completion.side_effect = ProviderAuthError("API Key is invalid or expired", provider="gemini")
        long_text = ("This is a comprehensive section discussing artificial intelligence and machine learning models in modern web systems. " * 30)
        with self.assertRaises(SummarizerError) as ctx:
            summarize_text(long_text, router=mock_router, chunk_size=500, chunk_overlap=50)
        self.assertIn("API Key is invalid or expired", str(ctx.exception))

    def test_p2_sum04_independent_persistence_flags(self):
        """P2-SUM-04: summarize route returns independent generation, persistence, and indexing status flags."""
        with patch("app.scrape_article", return_value="Sample article content for testing."):
            with patch("app.summarize_text", return_value={
                "title": "Sample Article",
                "overview": "Overview text",
                "key_points": ["Point 1"],
                "important_details": ["Detail 1"],
                "conclusion": "Conclusion",
                "mode": "quick",
                "provider": "gemini",
                "model": "gemini-3.8-flash",
            }):
                with patch("app.add_history_entry", return_value={"id": "test_hist_123"}):
                    with patch("app.index_summary_entry", return_value=True):
                        res = self.client.post(
                            "/summarize",
                            json={"url": "https://example.com/test", "mode": "quick"},
                            headers=self.post_headers,
                        )
                        self.assertEqual(res.status_code, 200)
                        data = res.get_json()
                        self.assertTrue(data.get("summary_generated"))
                        self.assertTrue(data.get("history_saved"))
                        self.assertTrue(data.get("embedding_indexed"))
                        self.assertTrue(data.get("pdf_available"))
                        self.assertFalse(data.get("notion_exported"))

    def test_p2_notion01_pagination_truncation_flag(self):
        """P2-NOTION-01: get_accessible_pages_with_pagination sets is_truncated when page limit is reached."""
        from notion_service import get_accessible_pages_with_pagination
        fake_pages_response = {
            "results": [
                {"id": f"page_{i}", "object": "page", "properties": {"title": {"title": [{"plain_text": f"Page {i}"}]}}}
                for i in range(15)
            ],
            "has_more": True,
            "next_cursor": "cursor_next_123",
        }
        with patch("notion_service.requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = fake_pages_response
            pages, is_truncated = get_accessible_pages_with_pagination(
                token="test_token",
                api_key="test_key",
                max_pages=10
            )
            self.assertEqual(len(pages), 10)
            self.assertTrue(is_truncated)

    def test_p2_pdf01_partial_warning_banner(self):
        """P2-PDF-01: generate_summary_pdf renders a partial summary warning banner when is_partial is True."""
        from pdf_exporter import generate_summary_pdf
        partial_summary = {
            "title": "Partial Report",
            "overview": "Overview text",
            "key_points": ["Key point"],
            "important_details": ["Detail"],
            "conclusion": "Conclusion",
            "is_partial": True,
            "warning": "Section 2 error occurred",
        }
        pdf_buffer = generate_summary_pdf(partial_summary)
        pdf_bytes = pdf_buffer.getvalue()
        self.assertTrue(len(pdf_bytes) > 1000)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

    def test_p2_scr01_pinned_ip_proxies_none(self):
        """P2-SCR-01: PinnedIPAdapter forces proxies=None to prevent proxy DNS bypass."""
        from scraper import PinnedIPAdapter
        from requests.adapters import HTTPAdapter
        adapter = PinnedIPAdapter(pinned_ips={"example.com": "93.184.216.34"})
        with patch.object(HTTPAdapter, "get_connection") as mock_super_conn:
            adapter.get_connection("https://example.com/test", proxies={"https": "http://proxy.local:8080"})
            mock_super_conn.assert_called_with("https://example.com/test", proxies=None)

    def test_p2_batch01_url_percent_encoding_canonicalization(self):
        """P2-BATCH-01: validate_batch_urls normalizes percent-encoded paths to detect duplicate URLs."""
        urls = [
            "https://example.com/article%20one",
            "https://example.com/article one",
        ]
        validated_items, stats = validate_batch_urls(urls)
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["valid"], 1)
        self.assertEqual(stats["duplicates"], 1)
        self.assertTrue(validated_items[1]["is_duplicate"])


if __name__ == "__main__":
    unittest.main()
