"""
WebBrief AI — PDF Exporter Module
Generates clean, multi-page, professional PDF summaries using ReportLab.
Includes metadata header, formatted sections, page numbering ('Page X of Y'),
and robust XML/Unicode character sanitization.
"""

import datetime
import html
import io
import re
from typing import Dict, Any, Optional

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
        HRFlowable,
        PageBreak,
    )
    from reportlab.pdfgen import canvas
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False
    letter = (612, 792)
    colors = None
    canvas = None


class NumberedCanvas(canvas.Canvas if REPORTLAB_AVAILABLE else object):
    """
    Two-pass canvas to dynamically compute and display total page count:
    'Page X of Y' in the running footer, along with clean running headers on subsequent pages.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, total_pages: int):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748b"))

        page_width, page_height = letter

        # Running Footer (all pages)
        self.setStrokeColor(colors.HexColor("#e2e8f0"))
        self.setLineWidth(0.6)
        self.line(40, 36, page_width - 40, 36)

        left_text = "WebBrief AI — Smart Structured Website Brief"
        right_text = f"Page {self._pageNumber} of {total_pages}"
        self.drawString(40, 24, left_text)
        self.drawRightString(page_width - 40, 24, right_text)

        # Running Header (pages 2+)
        if self._pageNumber > 1:
            self.setStrokeColor(colors.HexColor("#e2e8f0"))
            self.setLineWidth(0.5)
            self.line(40, page_height - 36, page_width - 40, page_height - 36)
            self.drawString(40, page_height - 30, "WebBrief AI • Executive Summary Brief")
            self.drawRightString(page_width - 40, page_height - 30, f"Page {self._pageNumber} of {total_pages}")

        self.restoreState()


def sanitize_text_for_pdf(text: Optional[str]) -> str:
    """
    Escapes XML special characters for ReportLab Paragraphs and
    normalizes unprintable control codes while preserving newlines and unicode.
    """
    if not text:
        return ""
    
    clean = str(text)
    # Strip non-printable ASCII control characters except \n, \r, \t
    clean = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", clean)
    # Escape XML entities for ReportLab XML parser
    clean = html.escape(clean, quote=False)
    # Convert double newlines into paragraph breaks
    clean = clean.replace("\n", "<br/>")
    return clean


def generate_summary_pdf(
    summary: Dict[str, Any],
    url: str = "",
    title: Optional[str] = None,
    mode: Optional[str] = None,
    created_at: Optional[str] = None,
) -> io.BytesIO:
    """
    Generates a professional PDF document from a structured summary dictionary.

    Returns:
        io.BytesIO: In-memory byte buffer containing the completed PDF.
    """
    if not REPORTLAB_AVAILABLE:
        raise RuntimeError("PDF export is unavailable because the reportlab library is not installed.")
    buffer = io.BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=40,
        rightMargin=40,
        topMargin=48,
        bottomMargin=48,
    )

    page_width, page_height = letter
    content_width = page_width - 80

    styles = getSampleStyleSheet()

    # Document Header Badge
    badge_style = ParagraphStyle(
        "DocBadge",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#4f46e5"),
        spaceAfter=6,
    )

    # Document Title
    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#0f172a"),
        spaceAfter=12,
    )

    # Meta Table Cell Styles
    meta_label_style = ParagraphStyle(
        "MetaLabel",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#64748b"),
    )
    meta_val_style = ParagraphStyle(
        "MetaVal",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#1e293b"),
    )

    # Section Headings
    section_h2 = ParagraphStyle(
        "SectionH2",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=16,
        textColor=colors.HexColor("#1e293b"),
        spaceBefore=14,
        spaceAfter=6,
        keepWithNext=True,
    )

    # Body Paragraph
    body_style = ParagraphStyle(
        "SectionBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=14.5,
        textColor=colors.HexColor("#334155"),
        spaceAfter=10,
    )

    # Bullet Points
    bullet_style = ParagraphStyle(
        "BulletItem",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=14,
        textColor=colors.HexColor("#334155"),
        leftIndent=14,
        firstLineIndent=-10,
        spaceAfter=5,
    )

    elements = []

    # 1. Brand Tag
    elements.append(Paragraph("WEBBRIEF AI • STRUCTURED SUMMARY REPORT", badge_style))

    # 2. Article Title
    raw_title = title or summary.get("title") or "Webpage Summary"
    elements.append(Paragraph(sanitize_text_for_pdf(raw_title), title_style))

    # 3. Metadata Table
    mode_display = summary.get("mode_name") or (mode.capitalize() if mode else "Quick Summary")
    date_display = created_at or datetime.datetime.now().strftime("%b %d, %Y • %I:%M %p")
    source_url_display = url or "Source URL not provided"

    # Make source URL clickable if valid HTTP
    if url and (url.startswith("http://") or url.startswith("https://")):
        safe_href = html.escape(url, quote=True)
        # Truncate visible text if too long to prevent overflowing
        visible_url = url if len(url) <= 80 else url[:77] + "..."
        safe_url_html = f'<a href="{safe_href}" color="#2563eb">{html.escape(visible_url)}</a>'
    else:
        safe_url_html = sanitize_text_for_pdf(source_url_display)

    meta_data = [
        [
            Paragraph("<b>MODE:</b>", meta_label_style),
            Paragraph(sanitize_text_for_pdf(mode_display), meta_val_style),
            Paragraph("<b>GENERATED:</b>", meta_label_style),
            Paragraph(sanitize_text_for_pdf(date_display), meta_val_style),
        ],
        [
            Paragraph("<b>SOURCE:</b>", meta_label_style),
            Paragraph(safe_url_html, meta_val_style),
            Paragraph("<b>TYPE:</b>", meta_label_style),
            Paragraph("Structured 5-Section Brief", meta_val_style),
        ]
    ]

    col_widths = [60, (content_width - 120) * 0.55, 75, (content_width - 120) * 0.45]
    meta_table = Table(meta_data, colWidths=col_widths)
    meta_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
        ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#e2e8f0")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#f1f5f9")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements.append(meta_table)
    elements.append(Spacer(1, 14))

    # Partial summary warning banner (P2-PDF-01)
    if summary.get("partial") or summary.get("is_partial"):
        warning_msg = summary.get("warning") or "Partial Summary: Some content sections encountered errors during generation."
        warn_style = ParagraphStyle(
            "PartialWarning",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=12,
            textColor=colors.HexColor("#b45309"),
        )
        warn_table = Table([[Paragraph(sanitize_text_for_pdf(f"⚠️ {warning_msg}"), warn_style)]], colWidths=[content_width])
        warn_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fef3c7")),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#f59e0b")),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        elements.append(warn_table)
        elements.append(Spacer(1, 10))

    # 4. Overview Section
    overview = summary.get("overview", "")
    if overview and str(overview).strip():
        elements.append(Paragraph("📌 Executive Overview", section_h2))
        elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e2e8f0"), spaceAfter=8))
        elements.append(Paragraph(sanitize_text_for_pdf(overview), body_style))
        elements.append(Spacer(1, 4))

    # 5. Key Points Section
    key_points = summary.get("key_points", [])
    if isinstance(key_points, list) and key_points:
        elements.append(Paragraph("⚡ Core Key Takeaways", section_h2))
        elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e2e8f0"), spaceAfter=8))
        for point in key_points:
            point_text = sanitize_text_for_pdf(point)
            if point_text:
                elements.append(Paragraph(f"• &nbsp; {point_text}", bullet_style))
        elements.append(Spacer(1, 8))

    # 6. Important Details Section
    details = summary.get("important_details", [])
    if isinstance(details, list) and details:
        elements.append(Paragraph("🔍 Supporting Context & Details", section_h2))
        elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e2e8f0"), spaceAfter=8))
        for detail in details:
            detail_text = sanitize_text_for_pdf(detail)
            if detail_text:
                elements.append(Paragraph(f"• &nbsp; {detail_text}", bullet_style))
        elements.append(Spacer(1, 8))

    # 7. Conclusion Section
    conclusion = summary.get("conclusion", "")
    if conclusion and str(conclusion).strip():
        elements.append(Paragraph("🎯 Final Conclusion & Impact", section_h2))
        elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e2e8f0"), spaceAfter=8))
        elements.append(Paragraph(sanitize_text_for_pdf(conclusion), body_style))

    # Build the document using the dynamic two-pass canvas
    doc.build(elements, canvasmaker=NumberedCanvas)
    buffer.seek(0)
    return buffer


def build_pdf_filename(title: Optional[str] = None) -> str:
    """
    Creates a safe, clean filename for downloaded PDFs, strictly neutralizing
    path traversal attempts (e.g. '../', null bytes) and non-alphanumeric characters.
    Example: 'WebBrief_Quantum_Breakthrough_20261106.pdf'
    """
    date_str = datetime.datetime.now().strftime("%Y%m%d")
    raw = (title or "Summary").strip()
    # Strip any control characters, directory separators, and traversal dots
    cleaned = re.sub(r"[\x00-\x1f\.\\/\:]", "", raw)
    # Retain strictly alphanumeric, underscores, and hyphens
    slug = re.sub(r"[^\w-]", "_", cleaned).strip("_")
    slug = re.sub(r"_+", "_", slug)[:40].strip("_")
    if not slug:
        slug = "Summary"
    return f"WebBrief_{slug}_{date_str}.pdf"


def generate_batch_summary_pdf(
    items: list[Dict[str, Any]],
    batch_title: str = "WebBrief AI — Batch Summaries Report",
) -> io.BytesIO:
    """
    Generates a single, consolidated multi-article PDF containing all completed
    summaries in a batch, separated by clean page breaks.
    """
    if not REPORTLAB_AVAILABLE:
        raise RuntimeError("PDF export is unavailable because the reportlab library is not installed.")
    buffer = io.BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=40,
        rightMargin=40,
        topMargin=48,
        bottomMargin=48,
    )

    page_width, page_height = letter
    content_width = page_width - 80

    styles = getSampleStyleSheet()

    badge_style = ParagraphStyle(
        "BatchBadge",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#4f46e5"),
        spaceAfter=6,
    )
    title_style = ParagraphStyle(
        "BatchTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=16,
        leading=20,
        textColor=colors.HexColor("#0f172a"),
        spaceAfter=10,
    )
    meta_label_style = ParagraphStyle(
        "BatchMetaLabel",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#64748b"),
    )
    meta_val_style = ParagraphStyle(
        "BatchMetaVal",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#1e293b"),
    )
    section_h2 = ParagraphStyle(
        "BatchSectionH2",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=15,
        textColor=colors.HexColor("#1e293b"),
        spaceBefore=12,
        spaceAfter=5,
        keepWithNext=True,
    )
    body_style = ParagraphStyle(
        "BatchSectionBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=13.5,
        textColor=colors.HexColor("#334155"),
        spaceAfter=8,
    )
    bullet_style = ParagraphStyle(
        "BatchBulletItem",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=13.5,
        textColor=colors.HexColor("#334155"),
        leftIndent=14,
        firstLineIndent=-10,
        spaceAfter=4,
    )

    elements = []
    completed_items = [it for it in items if it and (it.get("status") == "completed" or it.get("summary"))]

    if not completed_items:
        elements.append(Paragraph("WEBBRIEF AI • BATCH REPORT", badge_style))
        elements.append(Paragraph(sanitize_text_for_pdf(batch_title), title_style))
        elements.append(Paragraph("No completed summaries were available in this batch.", body_style))
        doc.build(elements, canvasmaker=NumberedCanvas)
        buffer.seek(0)
        return buffer

    for i, it in enumerate(completed_items):
        summary = it.get("summary") or {}
        raw_title = summary.get("title") or it.get("title") or f"Webpage Summary #{i+1}"
        url = it.get("url", "")
        mode_display = summary.get("mode_name") or it.get("mode_name") or "Standard Brief"
        date_display = it.get("created_at_formatted") or datetime.datetime.now().strftime("%b %d, %Y • %I:%M %p")

        # Item Header
        elements.append(Paragraph(f"BATCH SUMMARY {i+1} OF {len(completed_items)}", badge_style))
        elements.append(Paragraph(sanitize_text_for_pdf(raw_title), title_style))

        # Metadata Table
        if url and (url.startswith("http://") or url.startswith("https://")):
            safe_href = html.escape(url, quote=True)
            vis = url if len(url) <= 80 else url[:77] + "..."
            safe_url_html = f'<a href="{safe_href}" color="#2563eb">{html.escape(vis)}</a>'
        else:
            safe_url_html = sanitize_text_for_pdf(url or "URL not specified")

        meta_data = [
            [
                Paragraph("<b>MODE:</b>", meta_label_style),
                Paragraph(sanitize_text_for_pdf(mode_display), meta_val_style),
                Paragraph("<b>DATE:</b>", meta_label_style),
                Paragraph(sanitize_text_for_pdf(date_display), meta_val_style),
            ],
            [
                Paragraph("<b>SOURCE:</b>", meta_label_style),
                Paragraph(safe_url_html, meta_val_style),
                Paragraph("<b>ITEM:</b>", meta_label_style),
                Paragraph(f"#{i+1} in Batch Collection", meta_val_style),
            ]
        ]
        col_widths = [50, (content_width - 105) * 0.55, 55, (content_width - 105) * 0.45]
        meta_table = Table(meta_data, colWidths=col_widths)
        meta_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
            ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#e2e8f0")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#f1f5f9")),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        elements.append(meta_table)
        elements.append(Spacer(1, 10))

        # Overview
        overview = summary.get("overview", "")
        if overview and str(overview).strip():
            elements.append(Paragraph("📌 Executive Overview", section_h2))
            elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e2e8f0"), spaceAfter=6))
            elements.append(Paragraph(sanitize_text_for_pdf(overview), body_style))

        # Key Points
        key_points = summary.get("key_points", [])
        if isinstance(key_points, list) and key_points:
            elements.append(Paragraph("⚡ Core Key Takeaways", section_h2))
            elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e2e8f0"), spaceAfter=6))
            for pt in key_points:
                clean_pt = sanitize_text_for_pdf(pt)
                if clean_pt:
                    elements.append(Paragraph(f"• &nbsp; {clean_pt}", bullet_style))

        # Important Details
        details = summary.get("important_details", [])
        if isinstance(details, list) and details:
            elements.append(Paragraph("🔍 Supporting Context & Details", section_h2))
            elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e2e8f0"), spaceAfter=6))
            for dt in details:
                clean_dt = sanitize_text_for_pdf(dt)
                if clean_dt:
                    elements.append(Paragraph(f"• &nbsp; {clean_dt}", bullet_style))

        # Conclusion
        conclusion = summary.get("conclusion", "")
        if conclusion and str(conclusion).strip():
            elements.append(Paragraph("🎯 Final Conclusion & Impact", section_h2))
            elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e2e8f0"), spaceAfter=6))
            elements.append(Paragraph(sanitize_text_for_pdf(conclusion), body_style))

        # Page break between batch items except the last
        if i < len(completed_items) - 1:
            elements.append(PageBreak())

    doc.build(elements, canvasmaker=NumberedCanvas)
    buffer.seek(0)
    return buffer


def get_custom_pdf_styles():
    """Builds reusable typographic styles for comparative and batch ReportLab flowables."""
    base = getSampleStyleSheet()
    styles = {}
    styles["Title"] = ParagraphStyle(
        "CompTitle",
        parent=base["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=16,
        leading=20,
        textColor=colors.HexColor("#0f172a"),
        spaceAfter=10,
    )
    styles["SectionH2"] = ParagraphStyle(
        "CompH2",
        parent=base["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=16,
        textColor=colors.HexColor("#1e293b"),
        spaceBefore=12,
        spaceAfter=6,
        keepWithNext=True,
    )
    styles["SectionH3"] = ParagraphStyle(
        "CompH3",
        parent=base["Heading3"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=13,
        textColor=colors.HexColor("#334155"),
        spaceBefore=6,
        spaceAfter=3,
        keepWithNext=True,
    )
    styles["Body"] = ParagraphStyle(
        "CompBody",
        parent=base["Normal"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=14,
        textColor=colors.HexColor("#334155"),
        spaceAfter=6,
    )
    styles["Bullet"] = ParagraphStyle(
        "CompBullet",
        parent=base["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=13.5,
        textColor=colors.HexColor("#334155"),
        leftIndent=12,
        firstLineIndent=-12,
        spaceAfter=4,
    )
    styles["MetaVal"] = ParagraphStyle(
        "CompMeta",
        parent=base["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11.5,
        textColor=colors.HexColor("#64748b"),
    )
    return styles


def generate_comparison_pdf(
    comp: Optional[Dict[str, Any]] = None,
    comparison: Optional[Dict[str, Any]] = None,
    url_a: str = "",
    url_b: str = "",
    created_at: Optional[str] = None,
    **kwargs
) -> io.BytesIO:
    """
    Generates a beautifully styled, multi-page ReportLab PDF
    specifically structured for comparative research briefs.
    Accepts data as positional 'comp' or keyword 'comparison=', with optional url_a/url_b and created_at.
    """
    if not REPORTLAB_AVAILABLE:
        raise RuntimeError("PDF export is unavailable because the reportlab library is not installed.")

    comp = comp if comp is not None else (comparison or {})
    comp_data = comp
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=40,
        rightMargin=40,
        topMargin=45,
        bottomMargin=50,
    )

    styles = get_custom_pdf_styles()
    elements = []

    content_width = letter[0] - 80  # 612 - 80 = 532 pt

    art_a = dict(comp_data.get("article_a", {}))
    art_b = dict(comp_data.get("article_b", {}))
    if not art_a.get("url") and url_a:
        art_a["url"] = url_a
    if not art_b.get("url") and url_b:
        art_b["url"] = url_b

    title_a = sanitize_text_for_pdf(art_a.get("title", "Article A"))
    url_a = sanitize_text_for_pdf(art_a.get("url", ""))
    overview_a = sanitize_text_for_pdf(art_a.get("overview", ""))

    title_b = sanitize_text_for_pdf(art_b.get("title", "Article B"))
    url_b = sanitize_text_for_pdf(art_b.get("url", ""))
    overview_b = sanitize_text_for_pdf(art_b.get("overview", ""))

    comp_title = sanitize_text_for_pdf(comp_data.get("title", f"Comparative Analysis: {title_a} vs {title_b}"))
    date_str = created_at or datetime.datetime.now().strftime("%B %d, %Y • %I:%M %p")

    # Document Header Banner
    header_data = [
        [
            Paragraph("<b>WebBrief AI • Comparative Research Brief</b>", styles["MetaVal"]),
            Paragraph(f"<b>Generated:</b> {date_str}", styles["MetaVal"]),
        ]
    ]
    header_table = Table(header_data, colWidths=[content_width * 0.6, content_width * 0.4])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    elements.append(header_table)
    elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#6366f1"), spaceAfter=12))

    # Main Title
    elements.append(Paragraph(comp_title, styles["Title"]))
    elements.append(Spacer(1, 10))

    if comp.get("partial") or comp.get("is_partial"):
        warning_msg = comp.get("warning") or "Partial Comparative Analysis: Some content sections encountered errors during generation."
        warn_style = ParagraphStyle(
            "PartialComparisonWarning",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=12,
            textColor=colors.HexColor("#b45309"),
        )
        warn_table = Table([[Paragraph(sanitize_text_for_pdf(f"⚠️ {warning_msg}"), warn_style)]], colWidths=[content_width])
        warn_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fef3c7")),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#f59e0b")),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        elements.append(warn_table)
        elements.append(Spacer(1, 10))

    # Side-by-side Source Overview Cards Table
    url_a_html = f'<link href="{url_a}" color="#2563eb"><u>{url_a[:55]}...</u></link>' if len(url_a) > 55 else f'<link href="{url_a}" color="#2563eb"><u>{url_a}</u></link>'
    url_b_html = f'<link href="{url_b}" color="#2563eb"><u>{url_b[:55]}...</u></link>' if len(url_b) > 55 else f'<link href="{url_b}" color="#2563eb"><u>{url_b}</u></link>'

    card_a_content = [
        Paragraph("<b>SOURCE A</b>", styles["SectionH3"]),
        Paragraph(f"<b>Title:</b> {title_a}", styles["Body"]),
        Paragraph(f"<b>URL:</b> {url_a_html}", styles["MetaVal"]),
        Spacer(1, 4),
        Paragraph(f"<b>Overview:</b> {overview_a}", styles["Body"]),
    ]
    card_b_content = [
        Paragraph("<b>SOURCE B</b>", styles["SectionH3"]),
        Paragraph(f"<b>Title:</b> {title_b}", styles["Body"]),
        Paragraph(f"<b>URL:</b> {url_b_html}", styles["MetaVal"]),
        Spacer(1, 4),
        Paragraph(f"<b>Overview:</b> {overview_b}", styles["Body"]),
    ]

    sources_table = Table([[card_a_content, card_b_content]], colWidths=[content_width * 0.485, content_width * 0.485])
    sources_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#f8fafc")),
        ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#f8fafc")),
        ("BOX", (0, 0), (0, 0), 1, colors.HexColor("#cbd5e1")),
        ("BOX", (1, 0), (1, 0), 1, colors.HexColor("#cbd5e1")),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    elements.append(sources_table)
    elements.append(Spacer(1, 14))

    # 1. Common Ground & Shared Arguments
    common_points = comp.get("common_points", [])
    if common_points:
        elements.append(Paragraph("🤝 Common Arguments & Shared Information", styles["SectionH2"]))
        elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#cbd5e1"), spaceAfter=6))
        for cp in common_points:
            clean_cp = sanitize_text_for_pdf(cp)
            if clean_cp:
                elements.append(Paragraph(f"• &nbsp; {clean_cp}", styles["Bullet"]))
        elements.append(Spacer(1, 10))

    # 2. Contradictions & Disagreements (Crucial)
    contradictions = comp.get("contradictions", [])
    if contradictions:
        elements.append(Paragraph("⚠️ Contradictions & Disagreements", styles["SectionH2"]))
        elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#f87171"), spaceAfter=6))
        for cd in contradictions:
            clean_cd = sanitize_text_for_pdf(cd)
            if clean_cd:
                # Highlight in light red callout box
                callout_data = [[Paragraph(f"<b>CONFLICT:</b> {clean_cd}", styles["Body"])]]
                callout_t = Table(callout_data, colWidths=[content_width])
                callout_t.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fef2f2")),
                    ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#fca5a5")),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ]))
                elements.append(callout_t)
                elements.append(Spacer(1, 4))
        elements.append(Spacer(1, 10))

    # 3. Differing Perspectives
    perspectives = comp.get("differing_perspectives", [])
    if perspectives:
        elements.append(Paragraph("🎭 Differing Perspectives & Framing", styles["SectionH2"]))
        elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#cbd5e1"), spaceAfter=6))
        for p in perspectives:
            clean_p = sanitize_text_for_pdf(p)
            if clean_p:
                elements.append(Paragraph(f"• &nbsp; {clean_p}", styles["Bullet"]))
        elements.append(Spacer(1, 10))

    # 4. Key Facts & Claims Table
    claims = comp.get("key_facts_claims", [])
    if claims:
        elements.append(Paragraph("📊 Key Claims & Comparative Facts", styles["SectionH2"]))
        elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#cbd5e1"), spaceAfter=6))

        table_rows = [
            [
                Paragraph("<b>Topic / Point</b>", styles["SectionH3"]),
                Paragraph(f"<b>{title_a[:35]} Claim</b>", styles["SectionH3"]),
                Paragraph(f"<b>{title_b[:35]} Claim</b>", styles["SectionH3"]),
            ]
        ]
        for c in claims:
            table_rows.append([
                Paragraph(sanitize_text_for_pdf(c.get("topic", "Topic")), styles["Body"]),
                Paragraph(sanitize_text_for_pdf(c.get("article_a_claim", "")), styles["Body"]),
                Paragraph(sanitize_text_for_pdf(c.get("article_b_claim", "")), styles["Body"]),
            ])

        col_w = [content_width * 0.28, content_width * 0.36, content_width * 0.36]
        claims_table = Table(table_rows, colWidths=col_w)
        claims_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
            ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#cbd5e1")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        elements.append(claims_table)
        elements.append(Spacer(1, 10))

    # 5. Unique Information
    unique_a = comp.get("unique_to_a", [])
    unique_b = comp.get("unique_to_b", [])
    if unique_a or unique_b:
        elements.append(Paragraph("💎 Unique Information", styles["SectionH2"]))
        elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#cbd5e1"), spaceAfter=6))
        if unique_a:
            elements.append(Paragraph(f"<b>Present only in Source A ({title_a}):</b>", styles["Body"]))
            for ua in unique_a:
                elements.append(Paragraph(f"• &nbsp; {sanitize_text_for_pdf(ua)}", styles["Bullet"]))
            elements.append(Spacer(1, 4))
        if unique_b:
            elements.append(Paragraph(f"<b>Present only in Source B ({title_b}):</b>", styles["Body"]))
            for ub in unique_b:
                elements.append(Paragraph(f"• &nbsp; {sanitize_text_for_pdf(ub)}", styles["Bullet"]))
        elements.append(Spacer(1, 10))

    # 6. Conclusions Comparison
    conc = comp.get("conclusions_comparison", {})
    if conc:
        elements.append(Paragraph("🏁 Conclusions Comparison", styles["SectionH2"]))
        elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#cbd5e1"), spaceAfter=6))
        if conc.get("article_a_conclusion"):
            elements.append(Paragraph(f"<b>Source A Conclusion:</b> {sanitize_text_for_pdf(conc.get('article_a_conclusion'))}", styles["Body"]))
        if conc.get("article_b_conclusion"):
            elements.append(Paragraph(f"<b>Source B Conclusion:</b> {sanitize_text_for_pdf(conc.get('article_b_conclusion'))}", styles["Body"]))
        if conc.get("comparison_note"):
            elements.append(Paragraph(f"<b>Comparison Analysis:</b> {sanitize_text_for_pdf(conc.get('comparison_note'))}", styles["Body"]))
        elements.append(Spacer(1, 10))

    # 7. Overall Synthesis & Assessment
    synthesis = comp.get("synthesis", "")
    if synthesis:
        elements.append(Paragraph("🎯 Overall Synthesis & Assessment", styles["SectionH2"]))
        elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#10b981"), spaceAfter=6))
        synth_data = [[Paragraph(sanitize_text_for_pdf(synthesis), styles["Body"])]]
        synth_t = Table(synth_data, colWidths=[content_width])
        synth_t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#ecfdf5")),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#a7f3d0")),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ("LEFTPADDING", (0, 0), (-1, -1), 9),
            ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ]))
        elements.append(synth_t)

    doc.build(elements, canvasmaker=NumberedCanvas)
    buffer.seek(0)
    return buffer


