"""
WebBrief AI — Single-Page UI Consolidation Test Suite.
Validates:
1. Header navigation with two primary tabs: Summarize and History.
2. Summarize view as default containing BYOK, form modes, Custom Directives, and results.
3. History view containing Local Vector Knowledge as the first segment with side-by-side search button.
4. History list with instant keyword filtering, PDF export, Notion export, and deletion.
5. Integrated Notion modal with inline workspace connection.
6. Flask route redirection (/history -> /?view=history).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["FLASK_ENV"] = "development"
from app import app


class TestSinglePageUI(unittest.TestCase):

    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        index_path = os.path.join(base_dir, "templates", "index.html")
        with open(index_path, "r", encoding="utf-8") as f:
            self.index_html = f.read()

        css_path = os.path.join(base_dir, "static", "style.css")
        with open(css_path, "r", encoding="utf-8") as f:
            self.css = f.read()

    def test_01_two_primary_navigation_tabs(self):
        """Header must have two view-switcher tabs: Summarize and History."""
        self.assertIn('class="view-switcher-tabs"', self.index_html)
        self.assertIn('id="view-tab-summarize"', self.index_html)
        self.assertIn('id="view-tab-history"', self.index_html)
        self.assertIn("switchMainView('summarize')", self.index_html)
        self.assertIn("switchMainView('history')", self.index_html)

    def test_02_spa_view_containers(self):
        """Must define separate #view-summarize and #view-history view containers."""
        self.assertIn('id="view-summarize"', self.index_html)
        self.assertIn('id="view-history"', self.index_html)
        self.assertIn("switchMainView", self.index_html)

    def test_03_summarize_view_contains_directives_and_modes(self):
        """Summarize view must contain mode grid and collapsible Custom Prompt Directives."""
        self.assertIn('id="directives-accordion"', self.index_html)
        self.assertIn('id="custom-directive-input"', self.index_html)
        self.assertIn('id="directive-toggle-checkbox"', self.index_html)
        self.assertIn('id="save-directive-btn"', self.index_html)
        self.assertIn('id="reset-directive-btn"', self.index_html)

    def test_04_history_view_first_segment_vector_knowledge(self):
        """First segment of History must be Local Vector Knowledge with side-by-side search button."""
        self.assertIn("Local Vector Knowledge & Semantic Search", self.index_html)
        self.assertIn('class="vector-search-bar"', self.index_html)
        self.assertIn('id="history-semantic-query-input"', self.index_html)
        self.assertIn('id="history-semantic-search-btn"', self.index_html)
        self.assertIn('executeHistorySemanticSearch', self.index_html)
        self.assertIn('id="indexed-count-display"', self.index_html)
        self.assertIn('id="rebuild-index-btn"', self.index_html)

    def test_05_history_view_second_segment_history_list(self):
        """Second segment of History must list summaries with search button, filter, PDF, Notion, and delete."""
        self.assertIn('id="history-filter-input"', self.index_html)
        self.assertIn('id="history-filter-search-btn"', self.index_html)
        self.assertIn('id="history-container"', self.index_html)
        self.assertIn('id="clear-all-btn"', self.index_html)
        self.assertIn('exportHistoryItemToNotion', self.index_html)
        self.assertIn('deleteHistoryItem', self.index_html)

    def test_06_notion_modal_inline_workspace_connection(self):
        """Notion modal must support connecting workspace inline without navigating away."""
        self.assertIn('id="notion-modal"', self.index_html)
        self.assertIn('id="notion-modal-connect-view"', self.index_html)
        self.assertIn('id="notion-modal-token-input"', self.index_html)
        self.assertIn('id="notion-modal-connect-btn"', self.index_html)
        self.assertIn('connectNotionInline', self.index_html)
        self.assertIn('disconnectNotionInline', self.index_html)

    def test_07_route_history_redirects_to_spa_history_view(self):
        """GET /history must redirect to /?view=history."""
        resp = self.client.get("/history", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("view=history", resp.headers.get("Location", ""))

    def test_08_css_styles_for_single_page_consolidation(self):
        """CSS must define .view-switcher-tabs, .vector-search-bar, and responsive rules."""
        self.assertIn(".view-switcher-tabs", self.css)
        self.assertIn(".view-tab-btn", self.css)
        self.assertIn(".vector-search-bar", self.css)
        self.assertIn(".directives-accordion", self.css)
        self.assertIn(".mode-card:has(input[type=\"radio\"]:checked)", self.css)

    def test_09_interactive_buttons_and_handlers(self):
        """Verify buttons and JS handlers for theme toggle, submission tabs, and summary modes."""
        self.assertIn('id="theme-toggle-btn"', self.index_html)
        self.assertIn('toggleTheme', self.index_html)
        self.assertIn('setTheme', self.index_html)
        self.assertIn('switchSubmissionTab', self.index_html)
        self.assertIn('switchMainView', self.index_html)
        self.assertIn('selectSummaryMode', self.index_html)
        self.assertIn('tab-single-btn', self.index_html)
        self.assertIn('tab-batch-btn', self.index_html)
        self.assertIn('tab-compare-btn', self.index_html)
        self.assertIn('history-filter-search-btn', self.index_html)
        self.assertIn('history-semantic-search-btn', self.index_html)


if __name__ == "__main__":
    unittest.main()
