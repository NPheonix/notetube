go. Yes on flashcards.

One more feature: smart splitting for big PDFs.
- Raise the upload limit to 200 MB.
- Split the PDF by chapters using its bookmarks/outline (pypdf). Each part should contain only complete chapters and stay within the per-request limits. If the next chapter doesn't fit, it starts the next part. Only if one chapter alone is too big, split that chapter by pages.
- If the PDF has no bookmarks, try to detect chapter headings from page text; if that fails, fall back to page-based splitting.
- Show the parts before starting, like "Part 1: Chapters 1-4 (pages 1-62)".
- Let me download each part as a separate PDF file too.
- Don't add image compression; it can make scanned pages unreadable.
Test the splitting without using Gemini requests.
