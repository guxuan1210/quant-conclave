"""PDF report generator for QuantConclave advisory conversations and analysis reports.

Uses fpdf2 (v2.8.7+) with Unicode font support for Chinese text.
Registered font: FangSong (simfang.ttf) for CJK rendering, with Helvetica fallback.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

from fpdf import FPDF

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _find_chinese_font() -> tuple[str, str, str]:
    """Find a CJK font on Windows. Returns (family, filepath, style_key).

    Order: YaHei → SimSun → fpdf2 builtin fallback.
    """
    candidates = [
        ("FangSong", "C:/Windows/Fonts/simfang.ttf", ""),
        ("YaHei", "C:/Windows/Fonts/msyh.ttc", ""),
    ]
    for family, path, _ in candidates:
        if Path(path).exists():
            return (family, path, "M")
    # Last resort — try fangsong
    fallback = Path("C:/Windows/Fonts") / "simfang.ttf"
    if fallback.exists():
        return ("FangSong", str(fallback), "")
    return ("Helvetica", "", "")  # will fail with Unicode chars


class ChatPDF(FPDF):
    """PDF with CJK support for QuantConclave reports."""

    def __init__(self):
        super().__init__()
        self.set_auto_page_break(auto=True, margin=20)
        self._font_family = "Helvetica"
        self._chinese_available = False
        self._setup_font()

    def _setup_font(self):
        """Register the best available CJK font."""
        family, path, style = _find_chinese_font()
        if path and Path(path).exists():
            try:
                self.add_font(family, "", path)
                # Register same file for bold — fpdf2 synthesizes bold from
                # the regular glyph data when it can't find a dedicated bold face.
                self.add_font(family, "B", path)
                self._font_family = family
                self._chinese_available = True
                logger.info("Registered CJK font: %s (%s)", family, path)
            except Exception as exc:
                logger.warning("Failed to register CJK font %s: %s", path, exc)
                self._font_family = "Helvetica"

    def _set_cjk_font(self, style: str = "", size: int = 10):
        """Set font — CJK if available, Helvetica fallback."""
        if self._chinese_available:
            self.set_font(self._font_family, style, size)
        else:
            self.set_font("Helvetica", style, size)

    def header(self):
        self._set_cjk_font("B", 10)
        self.set_text_color(9, 105, 218)
        self.cell(0, 6, "QuantConclave Advisory Report", align="L")
        self.ln(3)
        self.set_draw_color(9, 105, 218)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self._set_cjk_font("", 7)
        self.set_text_color(128)
        self.cell(0, 8, f"Page {self.page_no()}/{{nb}}", align="C")

    def write_markdown_text(self, md_text: str):
        """Render markdown text into the PDF with compact spacing."""
        for line in md_text.split("\n"):
            stripped = line.rstrip()
            text = _strip_md(stripped)
            if not text and not stripped:
                self.ln(2)
                continue

            if stripped.startswith("# ") and not stripped.startswith("## "):
                # H1
                self.ln(3)
                self._set_cjk_font("B", 14)
                self.set_text_color(9, 105, 218)
                self.multi_cell(0, 6.5, text)
                self.set_text_color(51)
                self.ln(1)

            elif stripped.startswith("## "):
                # H2
                self.ln(2)
                self._set_cjk_font("B", 11)
                self.set_text_color(9, 105, 218)
                self.multi_cell(0, 5.5, text)
                self.set_text_color(51)
                self.ln(1)

            elif stripped.startswith("### "):
                # H3
                self.ln(2)
                self._set_cjk_font("B", 10)
                self.set_text_color(80)
                self.multi_cell(0, 5, text)
                self.set_text_color(51)

            elif stripped.startswith("---"):
                # horizontal rule
                self.ln(2)
                self.set_draw_color(200)
                self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
                self.ln(2)

            elif stripped.startswith("- ") or stripped.startswith("* "):
                # bullet
                self._set_cjk_font("", 9)
                self.set_text_color(51)
                self.multi_cell(self.w - self.l_margin - self.r_margin, 4.5, f"  - {text}")

            elif stripped.startswith("**") and len(stripped) < 100 and stripped.endswith("**"):
                # italic label
                self._set_cjk_font("", 8)
                self.set_text_color(120)
                self.multi_cell(0, 4, text)
                self.set_text_color(51)

            else:
                # normal paragraph
                self._set_cjk_font("", 9)
                self.set_text_color(51)
                self.multi_cell(0, 4.5, text)

        self.ln(2)


def _strip_md(text: str) -> str:
    """Strip markdown formatting tokens for PDF display — keeps Unicode intact."""
    if not text:
        return ""
    t = text
    # bold ** **
    t = re.sub(r'\*\*(.+?)\*\*', r'\1', t)
    # italic * *
    t = re.sub(r'\*(.+?)\*', r'\1', t)
    # inline code ` `
    t = re.sub(r'`([^`]+)`', r'\1', t)
    # links [text](url) -> text
    t = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', t)
    return t


def generate_chat_pdf(md_content: str) -> bytes:
    """Generate a PDF from chat/advisory markdown content."""
    pdf = ChatPDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.write_markdown_text(md_content)
    result = pdf.output()
    return bytes(result) if isinstance(result, bytearray) else result
