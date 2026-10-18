Add a PDF notes feature to NoteTube. Keep the code simple, well commented, and explain each step as you go. Start by showing me your plan before writing code.

GOAL
I upload a PDF (book chapter, study material, report, slides) and get full, detailed study notes, not a short summary. The notes should be good enough to study from without re-reading the PDF.

HOW IT FITS INTO THE APP
- Add two tabs at the top: "YouTube" (the existing feature, unchanged) and "PDF".
- Reuse the existing pieces: the API key handling, model constant and fallback list, quota/429 handling, language dropdown (English, Hinglish, Hindi), session history, and the Copy / .md / .txt / PDF download buttons.
- Don't break anything in the YouTube feature. Re-test it briefly after the change.

PDF INPUT
- Upload one PDF (show the size limit clearly). Show page count after upload.
- Optional page range (e.g. pages 10-35) so I can do one chapter at a time.
- Send the PDF to Gemini directly (it reads text, tables, images and scanned pages). Check the current Gemini API docs for how to send PDFs with the google-genai SDK and for the page and size limits.
- Free tier is about 20 requests per day and 250K tokens per minute, shared with the YouTube feature. Large PDFs must be split into chunks of pages that fit, processed one after another with a short pause, then combined. Show progress like "Part 2 of 4". Warn me before starting how many requests a PDF will use.

NOTE STYLES
1. Detailed study notes (default): follow the PDF's own chapter and section structure. For every section: key points, definitions, formulas, examples, and important numbers. Keep tables as Markdown tables. Add page references like (p. 12).
2. Exam revision: key concepts, formulas, likely questions with short answers, and common mistakes.
3. Quick summary: 8-12 bullets.
4. Flashcards: question/answer pairs I can download as CSV (works with Anki).

PROMPT QUALITY
- Notes must be complete and follow the order of the PDF. Do not skip sections. Do not invent facts that are not in the PDF; write "not mentioned in the PDF" where needed.
- Hinglish: natural Roman-script Hindi mixed with English technical terms, the way people actually text.
- When combining chunks, keep headings consistent and avoid repeating content.

DELIVERABLES
- Updated code, requirements.txt and README.md (add a PDF section).
- Test locally with a small PDF (5-10 pages) in English, then Hinglish, using as few Gemini requests as possible. Fix any errors and tell me how to run it.
- Then commit and push to GitHub so Streamlit Cloud updates. Ask me before pushing.

Ask me before installing anything beyond Python packages.
