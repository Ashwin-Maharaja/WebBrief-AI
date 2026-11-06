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
import urllib.parse
from typing import Dict, Any, Optional

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
    KeepTogether,
)
from reportlab.pdfgen import canvas


class NumberedCanvas(canvas.Canvas):
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

    Args:
        summary: Structured summary dict (title, overview, key_points, important_details, conclusion, mode_name, stats).
        url: Source webpage URL.
        title: Optional title override.
        mode: Summarization mode key or label.
        created_at: Formatted creation date/time string.

    Returns:
        io.BytesIO: In-memory byte buffer containing the completed PDF.
    """
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
    Creates a safe, clean filename for downloaded PDFs.
    Example: 'WebBrief_Quantum_Breakthrough_20261106.pdf'
    """
    date_str = datetime.datetime.now().strftime("%Y%m%d")
    raw = (title or "Summary").strip()
    slug = re.sub(r"[^\w\s-]", "", raw).strip()
    slug = re.sub(r"[-\s]+", "_", slug)[:40].strip("_")
    if not slug:
        slug = "Summary"
    return f"WebBrief_{slug}_{date_str}.pdf"
