"""
PDF helpers for NoteTube: open a PDF, find its chapters, and split it into
parts that are small enough for one Gemini request each.

How splitting works:
  1. Find chapters - from the PDF's bookmarks (outline) if it has them,
     otherwise by looking for headings like "Chapter 3" in the page text.
  2. Pack whole chapters into parts. If the next chapter doesn't fit in the
     current part, it starts a new part.
  3. Only a chapter that is too big on its own gets split by pages.
  4. If no chapters are found at all, split by pages.

Nothing here talks to Gemini, so all of it can be tested for free.
Pages are copied as they are - no image compression, so scanned pages stay readable.
"""

import math
import re
from dataclasses import dataclass, field
from io import BytesIO

from pypdf import PdfReader, PdfWriter

# Biggest PDF part we send to Gemini in one request. Gemini accepts up to
# 50 MB per PDF, but large inline requests are slow and can time out, so
# we stay well below that.
MAX_PART_BYTES = 20 * 1024 * 1024


@dataclass
class Chapter:
    title: str
    start: int          # first page, counting from 0
    end: int            # last page (included), counting from 0
    number: int = 0     # 1, 2, 3... (0 = front matter, before chapter 1)
    piece: tuple = None # (2, 3) = piece 2 of 3, when a big chapter is split by pages


@dataclass
class Part:
    start: int
    end: int
    chapters: list = field(default_factory=list)   # Chapter objects (or pieces of one)
    label: str = ""
    est_bytes: int = 0

    @property
    def pages(self) -> str:
        """Human page numbers, e.g. 'pages 1-62'."""
        return f"page {self.start + 1}" if self.start == self.end else f"pages {self.start + 1}-{self.end + 1}"


# ---------------------------------------------------------------------------
# Opening a PDF
# ---------------------------------------------------------------------------
def open_pdf(data: bytes) -> PdfReader:
    """Open PDF bytes. Raises ValueError with a friendly message if we can't."""
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            # Many books are "encrypted" with no password (copy protection
            # only) - decrypt("") opens those. A real password can't be opened.
            raise ValueError("This PDF is password-protected. Remove the password and upload it again.")
        len(reader.pages)  # forces pypdf to read the page list (fails on broken files)
        return reader
    except ValueError:
        raise
    except Exception as err:
        raise ValueError(f"This file couldn't be read as a PDF ({type(err).__name__}). "
                         "It may be damaged - try saving it again as PDF.") from err


def part_pdf_bytes(reader: PdfReader, start: int, end: int) -> bytes:
    """Make a new, smaller PDF containing pages start..end (from 0)."""
    writer = PdfWriter()
    for i in range(start, end + 1):
        writer.add_page(reader.pages[i])
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Finding chapters
# ---------------------------------------------------------------------------
def _add_ends(starts: list, total_pages: int) -> list:
    """Turn [(first_page, title), ...] into Chapter objects with end pages.

    Pages before the first chapter (cover, contents...) become "Front matter".
    """
    starts = sorted(starts)
    unique = []
    for page, title in starts:
        if not unique or page != unique[-1][0]:   # two bookmarks on one page -> keep the first
            unique.append((page, title))
    chapters = []
    if unique and unique[0][0] > 0:
        chapters.append(Chapter("Front matter", 0, unique[0][0] - 1, number=0))
    for i, (page, title) in enumerate(unique):
        end = unique[i + 1][0] - 1 if i + 1 < len(unique) else total_pages - 1
        chapters.append(Chapter(title, page, end, number=i + 1))
    return chapters


def chapters_from_bookmarks(reader: PdfReader) -> list:
    """Use the PDF's bookmarks (the outline you see in a PDF viewer's sidebar).

    We use the top level (usually chapters). If there's only one top-level
    bookmark (e.g. the book title), we use the level below it instead.
    """
    try:
        outline = reader.outline
    except Exception:
        return []

    def entries(level):
        return [item for item in level if not isinstance(item, list)]   # lists = sub-bookmarks

    items = entries(outline)
    if len(items) < 2:
        items = [child for item in outline if isinstance(item, list) for child in entries(item)]

    starts = []
    for item in items:
        try:
            page = reader.get_destination_page_number(item)
        except Exception:
            continue
        if page is not None and 0 <= page < len(reader.pages):
            starts.append((page, str(item.title).strip() or "Untitled"))
    return _add_ends(starts, len(reader.pages)) if len(starts) >= 2 else []


# A line that looks like a chapter heading: "Chapter 3", "CHAPTER IV", "Unit 2",
# "Lesson 5: ...", or Hindi "अध्याय 3" / "पाठ ३".
CHAPTER_HEADING = re.compile(
    r"^\s*(?:(chapter|chap\.|unit|lesson|module)\s+([0-9]+|[ivxlc]+)\b"
    r"|(अध्याय|पाठ|इकाई)\s*[-–:]?\s*([0-9०-९]+))",
    re.IGNORECASE,
)


def chapters_from_text(reader: PdfReader, first: int, last: int) -> list:
    """No bookmarks? Look for 'Chapter N' headings in the first lines of each page.

    Running headers (the same "Chapter 3" on top of every page) are handled
    by only counting a page when the chapter NUMBER changes.
    """
    starts, last_number = [], None
    for page in range(first, last + 1):
        try:
            text = reader.pages[page].extract_text() or ""
        except Exception:
            continue                        # unreadable page - skip it
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()][:5]
        for i, line in enumerate(lines):
            match = CHAPTER_HEADING.match(line)
            if not match:
                continue
            number = (match.group(2) or match.group(4)).lower()
            if number != last_number:
                title = line
                if len(line) < 15 and i + 1 < len(lines):   # "Chapter 3" alone -> add the next line
                    title = f"{line}: {lines[i + 1]}"
                starts.append((page, title[:80]))
                last_number = number
            break
    return _add_ends(starts, len(reader.pages)) if len(starts) >= 2 else []


# ---------------------------------------------------------------------------
# Packing chapters into parts
# ---------------------------------------------------------------------------
def _even_pieces(start: int, end: int, count: int) -> list:
    """Split pages start..end into `count` nearly equal pieces.
    e.g. 45 pages into 3 -> 15 + 15 + 15 (not 20 + 20 + 5)."""
    total = end - start + 1
    pieces, page = [], start
    for i in range(count):
        size = total // count + (1 if i < total % count else 0)
        pieces.append((page, page + size - 1))
        page += size
    return pieces


def plan_parts(reader: PdfReader, file_size: int, first: int, last: int, max_pages: int):
    """Decide how to split pages first..last (from 0) into parts.

    Returns (method, parts). method is "bookmarks", "headings" or "pages".
    Each part has at most `max_pages` pages and about MAX_PART_BYTES bytes.
    """
    total_pages = len(reader.pages)

    # Measuring a part's real size means writing it out, which is slow for big
    # files. Small files can't produce big parts, so for those we just estimate.
    must_measure = file_size > MAX_PART_BYTES * 0.9

    def size_of(start, end):
        if not must_measure:
            return file_size * (end - start + 1) // total_pages
        return len(part_pdf_bytes(reader, start, end))

    # 1. Find chapters (bookmarks first, then headings in the text).
    method, chapters = "bookmarks", chapters_from_bookmarks(reader)
    if not chapters:
        method, chapters = "headings", chapters_from_text(reader, first, last)
    if not chapters:
        method, chapters = "pages", [Chapter("Pages", first, last)]

    # 2. Keep only the chosen page range (a chapter may be cut at the edges).
    chapters = [Chapter(c.title, max(c.start, first), min(c.end, last), c.number)
                for c in chapters if c.end >= first and c.start <= last]

    # 3. Pack chapters into parts.
    parts, current, current_bytes = [], [], 0

    def close_current():
        nonlocal current, current_bytes
        if current:
            parts.append(Part(current[0].start, current[-1].end, current, est_bytes=current_bytes))
        current, current_bytes = [], 0

    for chapter in chapters:
        pages = chapter.end - chapter.start + 1
        size = size_of(chapter.start, chapter.end)

        if pages > max_pages or size > MAX_PART_BYTES:
            # This chapter alone is too big -> it gets split by pages.
            close_current()
            count = max(math.ceil(pages / max_pages), math.ceil(size / MAX_PART_BYTES))
            pieces = _even_pieces(chapter.start, chapter.end, count)
            # Check the real size of each piece; split again if still too big.
            while must_measure:
                sizes = [size_of(a, b) for a, b in pieces]
                if max(sizes) <= MAX_PART_BYTES or count >= pages:
                    break
                count = min(pages, count * 2)
                pieces = _even_pieces(chapter.start, chapter.end, count)
            for i, (a, b) in enumerate(pieces, 1):
                piece = Chapter(chapter.title, a, b, chapter.number, piece=(i, len(pieces)))
                parts.append(Part(a, b, [piece], est_bytes=size_of(a, b)))
        elif current and (chapter.end - current[0].start + 1 > max_pages
                          or current_bytes + size > MAX_PART_BYTES):
            # Doesn't fit in the current part -> start a new one.
            close_current()
            current, current_bytes = [chapter], size
        else:
            current.append(chapter)
            current_bytes += size
    close_current()

    for part in parts:
        part.label = make_label(part, method)
    return method, parts


def make_label(part: Part, method: str) -> str:
    """e.g. 'Chapters 1-4 (pages 1-62)' or 'Chapter 5, piece 2 of 3 (pages 80-99)'."""
    if method == "pages":
        return part.pages.capitalize()
    first = part.chapters[0]
    if first.piece:
        name = "Front matter" if first.number == 0 else f"Chapter {first.number}"
        return f"{name}, piece {first.piece[0]} of {first.piece[1]} ({part.pages})"
    numbers = [c.number for c in part.chapters if c.number > 0]
    names = ["Front matter"] if part.chapters[0].number == 0 else []
    if len(numbers) == 1:
        names.append(f"Chapter {numbers[0]}")
    elif numbers:
        names.append(f"Chapters {numbers[0]}-{numbers[-1]}")
    return f"{' + '.join(names)} ({part.pages})"
