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

import gc
import math
import re
from dataclasses import dataclass, field
from io import BytesIO, RawIOBase

from pypdf import PdfReader, PdfWriter

# Biggest PDF part we send to Gemini in one request. Gemini accepts up to
# 50 MB per PDF, but large inline requests are slow and can time out, so
# we stay well below that.
MAX_PART_BYTES = 20 * 1024 * 1024

# When measuring sizes, do this many pages at a time (keeps memory use low).
MEASURE_BATCH = 20


@dataclass
class Chapter:
    title: str
    start: int          # first page, counting from 0
    end: int            # last page (included), counting from 0
    number: int = None  # the chapter's number, e.g. 3 for "Chapter 3: Muscles";
                        # None for sections without one (Front matter, Preface, Index...)
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
def open_pdf(data) -> PdfReader:
    """Open a PDF. Raises ValueError with a friendly message if we can't.

    data can be the PDF's bytes (the app's uploads) or an open file, e.g.
    open("book.pdf", "rb") (the split_pdf.py tool). With an open file, pypdf
    reads from the disk only what it needs, so even a 500 MB book doesn't
    have to fit in memory. (Don't pass a file NAME: pypdf would then read the
    whole file into memory.)
    """
    try:
        reader = PdfReader(BytesIO(data) if isinstance(data, bytes) else data)
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


def _forget_read_pages(reader: PdfReader) -> None:
    """Free memory after each chapter/part, so a 500 MB book never has to fit
    in memory at once:
    - pypdf keeps every object it has read (images too) in a cache - we empty
      it; pypdf simply re-reads from the disk if it needs something again.
    - the finished writer's objects point at each other in loops, which only
      Python's "garbage collector" frees - so we run it now instead of later."""
    reader.resolved_objects.clear()
    gc.collect()


class _ByteCounter(RawIOBase):
    """A pretend file that only COUNTS the bytes written to it. Lets us measure
    how big a part would be without keeping a copy of it in memory."""
    def __init__(self):
        self.size = 0

    def writable(self):
        return True

    def write(self, data):
        self.size += len(data)
        return len(data)

    def tell(self):   # pypdf asks "where am I in the file?" - that's the bytes so far
        return self.size


def part_size(reader: PdfReader, start: int, end: int) -> int:
    """How many bytes a PDF of pages start..end would be (nothing is saved)."""
    writer = PdfWriter()
    for i in range(start, end + 1):
        writer.add_page(reader.pages[i])
    counter = _ByteCounter()
    writer.write(counter)
    _forget_read_pages(reader)
    return counter.size


# Saved inside each part made by split_pdf.py: the book page the part starts
# at. The app reads it to fill in "This file starts at book page" for you.
BOOK_START_KEY = "/NoteTubeBookStartPage"


def book_start_page(reader: PdfReader) -> int:
    """The book page this PDF starts at (from split_pdf.py), or 1 if unknown."""
    try:
        value = int(str((reader.metadata or {}).get(BOOK_START_KEY, 1)))
        return value if value >= 1 else 1
    except Exception:   # missing or broken - just start at page 1
        return 1


def write_part(reader: PdfReader, part, path) -> None:
    """Save one part as a PDF file, straight to disk.

    The part's chapters are added as bookmarks, so when you upload the part
    to NoteTube it can still split it by chapter. The book page the part
    starts at is saved too, so the notes can use real book page numbers.
    """
    writer = PdfWriter()
    for i in range(part.start, part.end + 1):
        writer.add_page(reader.pages[i])
    for chapter in part.chapters:
        if chapter.title == "Pages":   # page-based split: no real chapters
            continue
        title = chapter.title + (f" (piece {chapter.piece[0]} of {chapter.piece[1]})" if chapter.piece else "")
        writer.add_outline_item(title, chapter.start - part.start)
    # If this PDF was itself a part, add its own start page (so splitting twice still adds up).
    writer.add_metadata({BOOK_START_KEY: str(book_start_page(reader) + part.start)})
    with open(path, "wb") as f:
        writer.write(f)
    _forget_read_pages(reader)


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
        chapters.append(Chapter("Front matter", 0, unique[0][0] - 1))
    for i, (page, title) in enumerate(unique):
        end = unique[i + 1][0] - 1 if i + 1 < len(unique) else total_pages - 1
        chapters.append(Chapter(title, page, end, number=title_number(title)))
    # If no title has a number at all, just count them 1, 2, 3...
    if not any(c.number for c in chapters):
        count = 0
        for c in chapters:
            if c.title != "Front matter":
                count += 1
                c.number = count
    return chapters


# The number in a chapter title: "Chapter 3: Muscles", "3 Muscles", "3. Muscles",
# "Unit 3", "Lesson 3", "अध्याय ३" -> 3. ("Preface" or "Index" -> no number.)
TITLE_NUMBER = re.compile(
    r"^\s*(?:(?:chapter|chap\.?|ch\.?|unit|lesson|module|part|section|अध्याय|पाठ|इकाई)\s*[-–:.]?\s*)?"
    r"(\d+)(?!\.?\d)",
    re.IGNORECASE,
)


def title_number(title: str):
    """The chapter number written in a title, or None if it has none."""
    match = TITLE_NUMBER.match(title)
    return int(match.group(1)) if match else None   # int() also reads Hindi digits like ३


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


def plan_parts(reader: PdfReader, file_size: int, first: int, last: int, max_pages: int,
               max_bytes: int = MAX_PART_BYTES):
    """Decide how to split pages first..last (from 0) into parts.

    Returns (method, parts). method is "bookmarks", "headings" or "pages".
    Each part has at most `max_pages` pages and `max_bytes` bytes
    (the app uses MAX_PART_BYTES; the split_pdf.py tool uses 150 MB).
    """
    total_pages = len(reader.pages)

    # Measuring a part's real size means writing it out, which is slow for big
    # files. Small files can't produce big parts, so for those we just estimate.
    must_measure = file_size > max_bytes * 0.9

    def size_of(start, end):
        if not must_measure:
            return file_size * (end - start + 1) // total_pages
        # Measure 20 pages at a time and add up, so even a huge chapter never
        # sits in memory whole. (Shared things like fonts get counted once per
        # batch, so this can only OVER-estimate - parts stay under the limit.)
        return sum(part_size(reader, a, min(a + MEASURE_BATCH - 1, end))
                   for a in range(start, end + 1, MEASURE_BATCH))

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

        if pages > max_pages or size > max_bytes:
            # This chapter alone is too big -> it gets split by pages.
            close_current()
            count = max(math.ceil(pages / max_pages), math.ceil(size / max_bytes))
            pieces = _even_pieces(chapter.start, chapter.end, count)
            # Check the real size of each piece; split again if still too big.
            sizes = None
            while must_measure:
                sizes = [size_of(a, b) for a, b in pieces]
                if max(sizes) <= max_bytes or count >= pages:
                    break
                count = min(pages, count * 2)
                pieces = _even_pieces(chapter.start, chapter.end, count)
            for i, (a, b) in enumerate(pieces, 1):
                piece = Chapter(chapter.title, a, b, chapter.number, piece=(i, len(pieces)))
                measured = sizes[i - 1] if sizes else size_of(a, b)   # don't measure twice
                parts.append(Part(a, b, [piece], est_bytes=measured))
        elif current and (chapter.end - current[0].start + 1 > max_pages
                          or current_bytes + size > max_bytes):
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


def chapter_names(chapters: list, short: bool = False) -> list:
    """Name the chapters in a part, e.g. ['Front matter', 'Chapters 1-4'].
    short=True gives file-name style: ['FrontMatter', 'Ch1-4'].

    Numbered chapters next to each other are joined into a range; sections
    without a number (Preface, Index...) are named by their title.
    """
    names, numbers = [], []

    def add_numbers():
        if numbers:
            span = str(numbers[0]) if len(numbers) == 1 else f"{numbers[0]}-{numbers[-1]}"
            if short:
                names.append(f"Ch{span}")
            else:
                names.append(f"Chapter {span}" if len(numbers) == 1 else f"Chapters {span}")
            numbers.clear()

    for chapter in chapters:
        if chapter.number is None:
            add_numbers()
            if short:   # letters and digits only, e.g. "Front matter" -> "FrontMatter"
                slug = "".join(w[:1].upper() + w[1:] for w in re.findall(r"[A-Za-z0-9]+", chapter.title))
                names.append(slug[:20] or "Section")
            else:
                names.append(chapter.title[:30])
        else:
            numbers.append(chapter.number)
    add_numbers()
    if len(names) > 3:   # keep labels short
        names = names[:2] + [f"{len(names) - 2}more" if short else f"{len(names) - 2} more"]
    return names


def make_label(part: Part, method: str) -> str:
    """e.g. 'Chapters 1-4 (pages 1-62)' or 'Chapter 5, piece 2 of 3 (pages 80-99)'."""
    if method == "pages":
        return part.pages.capitalize()
    first = part.chapters[0]
    if first.piece:
        name = chapter_names([first])[0]
        return f"{name}, piece {first.piece[0]} of {first.piece[1]} ({part.pages})"
    return f"{' + '.join(chapter_names(part.chapters))} ({part.pages})"
