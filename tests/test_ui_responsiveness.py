"""
Unit and regression tests for UI responsiveness, overflow containment,
and feature-row batching across WebBrief AI styles and templates.
"""

import os
import re
import unittest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STATIC_CSS = os.path.join(BASE_DIR, "static", "style.css")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")


class TestUIResponsiveness(unittest.TestCase):
    """Test suite verifying media queries, overflow defenses, and responsive layouts."""

    @classmethod
    def setUpClass(cls):
        with open(STATIC_CSS, "r", encoding="utf-8") as f:
            cls.css_content = f.read()

        cls.template_files = {
            "index": os.path.join(TEMPLATES_DIR, "index.html"),
            "result": os.path.join(TEMPLATES_DIR, "result.html"),
            "history": os.path.join(TEMPLATES_DIR, "history.html"),
            "settings": os.path.join(TEMPLATES_DIR, "settings.html"),
            "setup": os.path.join(TEMPLATES_DIR, "setup.html"),
        }
        cls.templates = {}
        for name, path in cls.template_files.items():
            with open(path, "r", encoding="utf-8") as f:
                cls.templates[name] = f.read()

    # --------------------------------------------------------------------------
    # 1. Viewport Meta Tag Verification
    # --------------------------------------------------------------------------
    def test_all_templates_contain_viewport_meta(self):
        """Ensure all HTML templates specify width=device-width for proper mobile scaling."""
        viewport_regex = re.compile(
            r'<meta\s+name=["\']viewport["\']\s+content=["\'][^"\']*width=device-width[^"\']*["\']',
            re.IGNORECASE,
        )
        for name, content in self.templates.items():
            self.assertRegex(
                content,
                viewport_regex,
                f"Template '{name}.html' must declare a responsive viewport meta tag.",
            )

    # --------------------------------------------------------------------------
    # 2. Viewport & Container Overflow Defenses
    # --------------------------------------------------------------------------
    def test_html_and_body_prevent_horizontal_overflow(self):
        """Ensure html and body elements prevent horizontal viewport blowout."""
        self.assertIn("overflow-x: hidden;", self.css_content)
        self.assertIn("max-width: 100vw;", self.css_content)

        # Check html rule
        html_match = re.search(r"html\s*\{([^}]+)\}", self.css_content)
        self.assertIsNotNone(html_match, "CSS should declare an 'html' rule block.")
        self.assertIn("overflow-x: hidden", html_match.group(1))

        # Check body rule
        body_match = re.search(r"body\s*\{([^}]+)\}", self.css_content)
        self.assertIsNotNone(body_match, "CSS should declare a 'body' rule block.")
        self.assertIn("overflow-x: hidden", body_match.group(1))

    def test_global_text_and_code_overflow_wrapping(self):
        """Ensure text elements wrap words and code blocks scroll safely."""
        self.assertIn("overflow-wrap: break-word;", self.css_content)
        self.assertIn("word-break: break-word;", self.css_content)

        # Check pre/code styling
        code_match = re.search(r"pre,\s*code\s*\{([^}]+)\}", self.css_content)
        self.assertIsNotNone(code_match, "CSS should declare overflow safety for 'pre, code'.")
        self.assertIn("overflow-x: auto", code_match.group(1))

    def test_recent_item_title_prevents_fixed_width_blowout(self):
        """Ensure recent item title uses flexible truncation without rigid pixel caps."""
        title_match = re.search(r"\.recent-item-title\s*\{([^}]+)\}", self.css_content)
        self.assertIsNotNone(title_match, ".recent-item-title must be styled in CSS.")
        block = title_match.group(1)

        self.assertIn("text-overflow: ellipsis", block)
        self.assertIn("overflow: hidden", block)
        self.assertIn("white-space: nowrap", block)
        self.assertIn("flex: 1", block)
        self.assertIn("min-width: 0", block)
        self.assertNotIn("max-width: 480px", block, "Rigid 480px cap should be replaced by responsive flex sizing.")

    def test_claims_table_scroll_container_overflow(self):
        """Ensure claims table wrapper scrolls horizontally on mobile with touch support."""
        wrapper_match = re.search(r"\.claims-table-wrapper\s*\{([^}]+)\}", self.css_content)
        self.assertIsNotNone(wrapper_match, ".claims-table-wrapper must be styled in CSS.")
        block = wrapper_match.group(1)

        self.assertIn("overflow-x: auto", block)
        self.assertIn("-webkit-overflow-scrolling: touch", block)
        self.assertIn("max-width: 100%", block)

        # Claims table should maintain legible minimum width so it scrolls cleanly
        table_match = re.search(r"\.claims-table\s*\{([^}]+)\}", self.css_content)
        self.assertIsNotNone(table_match, ".claims-table must be styled in CSS.")
        self.assertIn("min-width:", table_match.group(1))

    def test_modal_card_overflow_protection(self):
        """Ensure modals do not exceed mobile viewport dimensions."""
        modal_match = re.search(r"\.notion-modal-card\s*\{([^}]+)\}", self.css_content)
        self.assertIsNotNone(modal_match, ".notion-modal-card must be styled in CSS.")
        block = modal_match.group(1)

        self.assertIn("max-height: 90vh", block)
        self.assertIn("overflow-y: auto", block)

    # --------------------------------------------------------------------------
    # 3. Media Query Breakpoints Verification
    # --------------------------------------------------------------------------
    def test_media_queries_defined_for_key_breakpoints(self):
        """Ensure 768px, 640px, and 480px breakpoints are explicitly present."""
        self.assertIn("@media (max-width: 768px)", self.css_content)
        self.assertIn("@media (max-width: 640px)", self.css_content)
        self.assertIn("@media (max-width: 480px)", self.css_content)

    # --------------------------------------------------------------------------
    # 4. Feature Row Batching Verification
    # --------------------------------------------------------------------------
    def test_tab_bar_batches_into_grid_on_mobile(self):
        """Ensure tab switcher converts from a flex row into a clean 2x2 grid on mobile."""
        mq_640 = self._extract_media_query("640px")
        self.assertIsNotNone(mq_640, "Media query for 640px must exist.")

        tab_match = re.search(r"\.tab-bar\s*\{([^}]+)\}", mq_640)
        self.assertIsNotNone(tab_match, ".tab-bar must be modified within 640px media query.")
        self.assertIn("grid-template-columns: repeat(2, 1fr)", tab_match.group(1))

        tab_btn_match = re.search(r"\.tab-btn\s*\{([^}]+)\}", mq_640)
        self.assertIsNotNone(tab_btn_match, ".tab-btn must have responsive rules within 640px media query.")
        self.assertIn("text-overflow: ellipsis", tab_btn_match.group(1))
        self.assertIn("overflow: hidden", tab_btn_match.group(1))

    def test_mode_selector_batches_responsively(self):
        """Ensure mode cards collapse from 4 cols -> 2 cols (768px) -> 1 col (640px)."""
        mq_768 = self._extract_media_query("768px")
        self.assertIsNotNone(mq_768)
        self.assertIn("grid-template-columns: repeat(2, 1fr)", mq_768)

        mq_640 = self._extract_media_query("640px")
        self.assertIsNotNone(mq_640)
        self.assertIn("grid-template-columns: 1fr", mq_640)

    def test_compare_grids_collapse_on_tablet_and_mobile(self):
        """Ensure comparison inputs and sources collapse to 1 column at <=768px."""
        mq_768 = self._extract_media_query("768px")
        self.assertIsNotNone(mq_768)
        self.assertRegex(
            mq_768,
            r"\.compare-inputs-grid[^{]*\{[^}]*grid-template-columns:\s*1fr",
        )
        self.assertRegex(
            mq_768,
            r"\.compare-sources-grid[^{]*\{[^}]*grid-template-columns:\s*1fr",
        )

        # Index template must not have hardcoded inline 2-col styles overriding CSS
        self.assertNotIn(
            'class="compare-inputs-grid" style="display: grid; grid-template-columns: 1fr 1fr',
            self.templates["index"],
        )
        self.assertNotIn(
            'style="display: grid; grid-template-columns: 1fr 1fr; gap: 14px;" class="compare-sources-grid"',
            self.templates["index"],
        )

    def test_hosted_byok_row_batches_vertically_on_mobile(self):
        """Ensure hosted BYOK input row stacks neatly on small screens."""
        mq_640 = self._extract_media_query("640px")
        self.assertIsNotNone(mq_640)

        self.assertRegex(
            mq_640,
            r"\.hosted-key-row\s*\{[^}]*flex-direction:\s*column",
        )
        self.assertRegex(
            mq_640,
            r"#hosted-provider-select\s*\{[^}]*width:\s*100%",
        )

    def test_search_controls_row_batches_vertically_on_mobile(self):
        """Ensure semantic search filter and threshold controls stack on small screens."""
        mq_640 = self._extract_media_query("640px")
        self.assertIsNotNone(mq_640)

        self.assertRegex(
            mq_640,
            r"\.search-controls-row\s*\{[^}]*flex-direction:\s*column",
        )

    def test_actions_and_buttons_stack_full_width_on_mobile(self):
        """Ensure action button groups stretch to 100% width on touchscreens."""
        mq_640 = self._extract_media_query("640px")
        self.assertIsNotNone(mq_640)

        self.assertRegex(
            mq_640,
            r"\.result-actions\s*\{[^}]*flex-direction:\s*column",
        )
        self.assertRegex(
            mq_640,
            r"\.batch-results-header\s*\{[^}]*flex-direction:\s*column",
        )

    def test_compact_mobile_breakpoint_refinements(self):
        """Ensure 480px breakpoint optimizes spacing and font sizes for smaller phones."""
        mq_480 = self._extract_media_query("480px")
        self.assertIsNotNone(mq_480)

        self.assertIn("padding: 12px 8px;", mq_480)
        self.assertIn(".tab-btn", mq_480)
        self.assertIn(".claims-table", mq_480)
        self.assertIn(".stat-pills-row", mq_480)

    # --------------------------------------------------------------------------
    # Helper Methods
    # --------------------------------------------------------------------------
    def _extract_media_query(self, breakpoint_str: str) -> str:
        """Extract the content inside an @media block for a given breakpoint."""
        pattern = rf"@media\s*\([^)]*{breakpoint_str}[^)]*\)\s*\{{"
        match = re.search(pattern, self.css_content)
        if not match:
            return ""

        start = match.end()
        brace_count = 1
        pos = start

        while pos < len(self.css_content) and brace_count > 0:
            if self.css_content[pos] == "{":
                brace_count += 1
            elif self.css_content[pos] == "}":
                brace_count -= 1
            pos += 1

        return self.css_content[start : pos - 1]


if __name__ == "__main__":
    unittest.main()
