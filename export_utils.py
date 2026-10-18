"""
Helpers that turn the Markdown notes into a plain .txt file and a clean PDF.

The PDF uses free Noto fonts from the 'fonts' folder:
  - Noto Sans            -> English / Hinglish text
  - Noto Sans Devanagari -> Hindi text (used automatically when needed)
"Text shaping" (via the uharfbuzz package) joins Hindi letters and matras
correctly - without it, Hindi words would look broken.
"""

import re
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

FONT_DIR = Path(__file__).parent / "fonts"

# Emojis and similar symbols aren't in our fonts, so we remove them from the PDF.
EMOJI_PATTERN = re.compile("[\U0001F000-\U0001FFFF☀-➿️‍]")

HEADING = re.compile(r"^(#{1,6})\s+(.*)")
BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)")
NUMBERED = re.compile(r"^(\s*)(\d+[.)])\s+(.*)")


# ---------------------------------------------------------------------------
# Inline Markdown clean-up (shared by .txt and PDF)
# ---------------------------------------------------------------------------
def clean_inline(text: str, keep_bold: bool) -> str:
    """Remove Markdown symbols inside a line.

    keep_bold=True keeps **bold** markers, because the PDF library can
    print those as real bold text.
    """
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)    # [label](link) -> label (link)
    text = re.sub(r"`([^`]*)`", r"\1", text)                         # `code` -> code
    text = re.sub(r"__(.+?)__", r"**\1**", text)                     # __bold__ -> **bold**
    text = re.sub(r"(?<![*\w])\*(?!\*)(.+?)(?<!\*)\*(?![*\w])", r"\1", text)  # *italic* -> italic
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"\1", text)              # _italic_ -> italic
    if not keep_bold:
        text = text.replace("**", "")
    return text


def markdown_to_plain_text(md: str) -> str:
    """Readable plain text: headings in CAPS, bullets as '•'."""
    out = []
    for line in md.splitlines():
        if m := HEADING.match(line):
            title = clean_inline(m.group(2), keep_bold=False)
            out += ["", title.upper() if len(m.group(1)) <= 2 else title, ""]
        elif m := BULLET.match(line):
            out.append(f"{m.group(1)}• {clean_inline(m.group(2), keep_bold=False)}")
        elif re.fullmatch(r"\s*([-*_])(\s*\1){2,}\s*", line):  # --- divider
            out.append("-" * 40)
        elif re.fullmatch(r"\s*\|[\s|:-]+", line):              # |---|---| table separator
            continue
        else:
            out.append(clean_inline(line.lstrip("> "), keep_bold=False))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
class NotesPDF(FPDF):
    def footer(self):
        """Page number at the bottom of every page."""
        self.set_y(-12)
        self.set_font("NotoSans", size=8)
        self.set_text_color(140, 140, 140)
        self.cell(0, 8, f"NoteTube  ·  page {self.page_no()}", align="C")


def _new_pdf() -> NotesPDF:
    pdf = NotesPDF(format="A4")
    pdf.set_margins(18, 18, 18)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_font("NotoSans", "", FONT_DIR / "NotoSans-Regular.ttf")
    pdf.add_font("NotoSans", "B", FONT_DIR / "NotoSans-Bold.ttf")
    pdf.add_font("NotoDeva", "", FONT_DIR / "NotoSansDevanagari-Regular.ttf")
    pdf.add_font("NotoDeva", "B", FONT_DIR / "NotoSansDevanagari-Bold.ttf")
    # When a character (like Hindi) is missing from Noto Sans, use Noto Sans Devanagari.
    pdf.set_fallback_fonts(["NotoDeva"])
    pdf.set_text_shaping(True)
    pdf.add_page()
    return pdf


def _write(pdf, text, size, bold=False, color=(30, 30, 30), indent=0.0, space_after=1.5, line_h=None):
    """Write one wrapped paragraph. '**bold**' inside the text becomes bold."""
    pdf.set_font("NotoSans", "B" if bold else "", size)
    pdf.set_text_color(*color)
    pdf.set_x(pdf.l_margin + indent)
    pdf.multi_cell(pdf.epw - indent, line_h or size * 0.55, text, markdown=True,
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(space_after)


def _list_item(pdf, marker, text, level):
    """A bullet or numbered item with a hanging indent, so wrapped lines line up."""
    indent = 4 + level * 6
    marker_w = 6 if marker == "•" else 8
    pdf.set_font("NotoSans", "", 11)
    pdf.set_text_color(30, 30, 30)
    pdf.set_x(pdf.l_margin + indent)
    pdf.cell(marker_w, 6.2, marker)
    pdf.multi_cell(pdf.epw - indent - marker_w, 6.2, text, markdown=True,
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1)


def markdown_to_pdf(md: str, title: str, video_url: str, date_text: str, subtitle: str = "") -> bytes:
    """Build the PDF and return it as bytes (ready for a download button)."""
    pdf = _new_pdf()

    # --- Header: title, link, date ---
    title = re.sub(r"\s{2,}", " ", EMOJI_PATTERN.sub("", title)).strip()
    _write(pdf, title or "Notes", 18, bold=True, space_after=2, line_h=9)
    if video_url:
        pdf.set_font("NotoSans", "", 10)
        pdf.set_text_color(30, 90, 200)
        pdf.cell(0, 6, video_url, link=video_url, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    meta = "  ·  ".join(p for p in [subtitle, date_text] if p)
    _write(pdf, meta, 10, color=(110, 110, 110), space_after=3)
    pdf.set_draw_color(200, 200, 200)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(5)

    # --- Body: go through the notes line by line ---
    in_code_block = False
    for raw in md.splitlines():
        line = re.sub(r"(?<=\S) {2,}", " ", EMOJI_PATTERN.sub("", raw)).rstrip()
        if re.fullmatch(r"\s*\|[\s|:-]+", line):
            continue  # the |---|---| row under a table header - nothing to print
        # fpdf treats '--' as strikethrough; Markdown doesn't, so turn it into a dash.
        line = line.replace("--", "–") if not re.fullmatch(r"\s*-{3,}\s*", line) else line

        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if not line.strip():
            pdf.ln(1.5)
            continue
        if in_code_block:
            _write(pdf, line, 10, color=(60, 60, 60), indent=4, space_after=0.5)
            continue

        if m := HEADING.match(line):
            level = len(m.group(1))
            text = clean_inline(m.group(2), keep_bold=False)
            size = {1: 16, 2: 14, 3: 12.5}.get(level, 11.5)
            pdf.ln(3 if level <= 2 else 1.5)
            _write(pdf, text, size, bold=True, color=(20, 20, 20), space_after=1.5, line_h=size * 0.6)
        elif re.fullmatch(r"\s*([-*_])(\s*\1){2,}\s*", line):            # --- divider line
            pdf.ln(1)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
            pdf.ln(3)
        elif m := BULLET.match(line):
            _list_item(pdf, "•", clean_inline(m.group(2), keep_bold=True), len(m.group(1)) // 2)
        elif m := NUMBERED.match(line):
            _list_item(pdf, m.group(2), clean_inline(m.group(3), keep_bold=True), len(m.group(1)) // 2)
        elif line.lstrip().startswith(">"):                               # > quote
            _write(pdf, clean_inline(line.lstrip("> "), keep_bold=True), 11,
                   color=(90, 90, 90), indent=6, space_after=1.5)
        elif line.strip().startswith("|"):                                # table row
            cells = [clean_inline(c.strip(), keep_bold=True) for c in line.strip().strip("|").split("|")]
            _write(pdf, "   |   ".join(cells), 10.5, space_after=1)
        else:
            _write(pdf, clean_inline(line, keep_bold=True), 11, space_after=1.5, line_h=6.2)

    return bytes(pdf.output())
