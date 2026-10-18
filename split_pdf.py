"""
split_pdf.py - split a BIG PDF into smaller parts on your own computer.

Use it for PDFs that are too big to upload to NoteTube (e.g. 500 MB books).
It splits chapter by chapter, the same way the app does (see pdf_tools.py):
whole chapters are kept together, and only a chapter that is too big on its
own is split by pages. Each part is under 150 MB, ready to upload.

How to run it (in PowerShell, in the NoteTube folder):
    .\\venv\\Scripts\\python.exe split_pdf.py "C:\\path\\to\\book.pdf"

The parts are saved in a new folder next to the PDF, e.g.
    C:\\path\\to\\book_parts\\Part1_Ch1-4.pdf
                         Part2_Ch5-7.pdf ...

Nothing is sent to Gemini - this only works on your computer.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

from pdf_tools import chapter_names, open_pdf, plan_parts, write_part

MAX_PART_MB = 150       # each part stays under this size
MAX_PART_PAGES = 1000   # NoteTube (Gemini) reads at most 1,000 pages per file

METHOD_TEXT = {
    "bookmarks": "Split by chapter, using the PDF's bookmarks.",
    "headings": "No bookmarks, so chapters were found from headings in the text.",
    "pages": "No chapters found, so the PDF is split by pages.",
}


def mb_text(size: int) -> str:
    """e.g. 499 MB, or 2.4 MB for small files."""
    return f"{size / 1e6:.0f} MB" if size >= 10e6 else f"{size / 1e6:.1f} MB"


def part_file_name(number: int, part, method: str) -> str:
    """A clear file name, e.g. Part1_Ch1-4.pdf, Part3_Ch5_piece1of2.pdf,
    Part1_FrontMatter_Ch1-3.pdf, or Part2_p201-400.pdf (page-based split)."""
    if method == "pages":
        return f"Part{number}_p{part.start + 1}-{part.end + 1}.pdf"
    first = part.chapters[0]
    if first.piece:   # one big chapter, split by pages
        name = chapter_names([first], short=True)[0]
        return f"Part{number}_{name}_piece{first.piece[0]}of{first.piece[1]}.pdf"
    return f"Part{number}_{'_'.join(chapter_names(part.chapters, short=True))}.pdf"


def main() -> int:
    # Windows terminals sometimes can't print Hindi chapter titles; this avoids a crash.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.getLogger("pypdf").setLevel(logging.ERROR)   # hide pypdf's technical warnings

    parser = argparse.ArgumentParser(description="Split a big PDF into chapter-wise parts for NoteTube.")
    parser.add_argument("pdf", help='the PDF to split, e.g. "C:\\Books\\anatomy.pdf"')
    parser.add_argument("--max-mb", type=float, default=MAX_PART_MB,
                        help=f"biggest part size in MB (default {MAX_PART_MB})")
    args = parser.parse_args()

    pdf_path = Path(args.pdf.strip('"')).expanduser()
    if not pdf_path.is_file():
        print(f"❌ File not found: {pdf_path}")
        print('   Tip: put the path in "quotes", and check the spelling.')
        return 1
    max_bytes = int(args.max_mb * 1024 * 1024)
    file_size = pdf_path.stat().st_size
    started = time.time()

    # Open the PDF as a FILE (not by name) so pypdf reads from disk as needed
    # instead of loading the whole book into memory.
    with open(pdf_path, "rb") as pdf_file:
        try:
            reader = open_pdf(pdf_file)
        except ValueError as err:
            print(f"❌ {err}")
            return 1
        page_count = len(reader.pages)
        print(f"📄 {pdf_path.name}: {page_count} pages, {mb_text(file_size)}")

        if file_size <= max_bytes and page_count <= MAX_PART_PAGES:
            print(f"✅ This PDF is already under {args.max_mb:.0f} MB - upload it to NoteTube as it is.")
            return 0

        # 1. Plan the parts (this measures chapter sizes, so it can take a minute for big files).
        print("🔎 Finding chapters and measuring their sizes (big files can take a minute)...")
        method, parts = plan_parts(reader, file_size, 0, page_count - 1, MAX_PART_PAGES, max_bytes)
        print(f"   {METHOD_TEXT[method]} → {len(parts)} parts")

        # 2. Save each part into a folder next to the PDF.
        out_dir = pdf_path.parent / f"{pdf_path.stem}_parts"
        out_dir.mkdir(exist_ok=True)
        old_parts = list(out_dir.glob("Part*.pdf"))
        if old_parts:
            print(f"   Note: {out_dir.name} already has {len(old_parts)} part file(s); "
                  "files with the same names will be replaced.")
        print(f"💾 Saving parts to: {out_dir}")

        too_big = []
        for number, part in enumerate(parts, 1):
            name = part_file_name(number, part, method)
            write_part(reader, part, out_dir / name)
            size = (out_dir / name).stat().st_size
            pages = part.end - part.start + 1
            print(f"   ✅ {name:32} pages {part.start + 1}-{part.end + 1} ({pages} pages), {mb_text(size)}")
            if size > max_bytes:
                too_big.append(name)   # only possible if a SINGLE page is bigger than the limit

    print(f"\n🎉 Done in {time.time() - started:.0f} s. {len(parts)} parts saved in {out_dir}")
    if too_big:
        print(f"⚠️ Still over {args.max_mb:.0f} MB (a single page is that big): {', '.join(too_big)}")
    print("Next: upload the parts one at a time in NoteTube's 📄 PDF tab.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
