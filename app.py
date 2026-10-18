"""
NoteTube - turn a YouTube link or a PDF into clean notes.

How the app works (big picture):
YouTube tab
  1. You paste a YouTube link and pick a language + note style.
  2. We ask Google's Gemini AI to "watch" the video directly from its URL.
  3. If that fails, we download the video's captions (transcript) and send
     that text to Gemini instead.
  4. If that also fails, you can paste a transcript yourself.
PDF tab
  1. You upload a PDF and (optionally) choose a page range.
  2. Big PDFs are split into parts by chapter (see pdf_tools.py).
  3. Each part is sent to Gemini, one after another, and the notes are joined.
Both tabs: the notes are shown on screen and you can copy or download them.

Run it with:   streamlit run app.py
"""

import csv
import hashlib
import io
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from functools import partial

import streamlit as st
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api import _errors as yt_errors

from export_utils import markdown_to_pdf, markdown_to_plain_text
from pdf_tools import open_pdf, part_pdf_bytes, plan_parts

# ---------------------------------------------------------------------------
# 1. SETTINGS - change these if Google renames or retires a model.
#    Current model names: https://ai.google.dev/gemini-api/docs/models
# ---------------------------------------------------------------------------
MODEL_NAME = "gemini-3.8-flash"  # the model we try first

# If MODEL_NAME hits its free limit (or is unavailable), we try these in order.
# All of them are on Gemini's free tier, and each model has its OWN separate
# free limits - so when one is used up for the day, the next one still works.
# (Free tier list: https://ai.google.dev/gemini-api/docs/pricing)
FALLBACK_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]

# A pasted key equal to this placeholder is treated as "no key".
PLACEHOLDER_KEY = "PASTE_YOUR_KEY_HERE"

LANGUAGES = ["English", "Hinglish", "Hindi"]
NOTE_STYLES = [
    "Quick summary",
    "Detailed study notes",
    "Action items and takeaways",
    "Content ideas for CPV FitLab",
]

# PDF note styles, and how many PDF pages we send per Gemini request for each.
# Detailed styles get fewer pages per request, so Gemini has room to cover
# every section properly instead of squeezing.
PDF_STYLES = {
    "Detailed study notes": 20,
    "Exam revision": 30,
    "Quick summary": 150,
    "Flashcards (Anki CSV)": 25,
}
PAUSE_BETWEEN_PARTS = 10   # seconds; keeps us under the free 5-requests-per-minute limit
MAX_UPLOAD_MB = 200        # must match maxUploadSize in .streamlit/config.toml
FREE_REQUESTS_PER_DAY = 20

# ---------------------------------------------------------------------------
# 2. PROMPTS - the instructions we give Gemini.
#    Final prompt = BASE_RULES + one style block + one language block.
# ---------------------------------------------------------------------------
BASE_RULES = """You are NoteTube, a careful note-taker who turns YouTube videos into clear notes.

Strict rules:
- Use ONLY information that is actually in the video. Do not add facts, numbers, names, studies or advice from your own knowledge.
- If the requested format asks for something the video does not cover, write "Not mentioned in the video" for that part instead of guessing.
- Never invent quotes or timestamps. Only give a timestamp (mm:ss) if you can see it in the video or transcript.
- Write in clean Markdown: '#'/'##' headings, '-' bullets, '**bold**' for key terms. No HTML, no code blocks.
- Start directly with the notes. No greeting, no "Here are your notes", no closing remarks.
"""

STYLE_PROMPTS = {
    "Quick summary": """Format: QUICK SUMMARY
- Start with one '## ' heading that names the video's topic.
- Then 5 to 8 bullet points with the most important ideas, in the order the video presents them.
- Each bullet is one or two short sentences. Bold the key term in each bullet.
- End with a one-line '**Bottom line:**' that captures the main message.""",

    "Detailed study notes": """Format: DETAILED STUDY NOTES
- Start with '# ' + a clear title for the topic, then a 2-3 sentence overview.
- Split the content into logical sections with '## ' headings, following the video's order. Add (mm:ss) after a heading when the timestamp is available.
- Under each section: key points as bullets, with sub-bullets for details.
- Add a '## Key definitions' section: '**Term** - meaning as explained in the video'. If no terms are defined, write "Not mentioned in the video".
- Add a '## Examples from the video' section with the examples the speaker actually used.
- End with '## Quick revision' - 3 to 5 bullets a student could memorise.""",

    "Action items and takeaways": """Format: ACTION ITEMS AND TAKEAWAYS
- '## Key takeaways' - 4 to 7 bullets with the most useful lessons.
- '## Action items' - a numbered list of concrete steps the viewer can do, each starting with a verb. Only include actions the video recommends or clearly implies. If there are none, write "Not mentioned in the video".
- '## Common mistakes to avoid' - only if the video mentions them, otherwise write "Not mentioned in the video".
- '## Resources mentioned' - tools, books, links or people named in the video, or "Not mentioned in the video".""",

    "Content ideas for CPV FitLab": """Format: CONTENT IDEAS FOR CPV FITLAB
CPV FitLab is a fitness coaching brand on Instagram. Turn the video's ideas into content for it.
- Start with '## Core ideas from the video' - 3 to 5 bullets summarising what the video actually says.
- '## 5 Instagram carousel ideas' - for each idea use a '### ' heading with a catchy carousel title, then:
  - **Slide 1 hook:** the cover line
  - **Slides 2-6:** one bullet per slide with the point
  - **Last slide CTA:** e.g. save/share/DM for coaching
  - **Caption idea:** one or two lines
- '## 3 Reels hooks' - numbered. For each: **Spoken hook** (first 3 seconds), **On-screen text**, and **Video idea** in one line.
- Every idea must be based on points made in the video. If the video is not about fitness, connect ideas to fitness only where it is honest, and say so in one line at the top.""",
}

LANGUAGE_PROMPTS = {
    "English": "Language: write everything in simple, clear English.",

    "Hinglish": """Language: write in natural HINGLISH - Hindi written in Roman (English) letters, mixed with English words, the way people in India actually text on WhatsApp.
- Use Roman script ONLY. Never use Devanagari letters.
- Keep technical terms, names and common words in English (protein, workout, deadline, budget, etc.).
- Example tone: "Is video mein bataya gaya hai ki daily 30 min walk karna kaafi helpful hai, especially fat loss ke liye."
- Keep it friendly and easy, not formal or 'shuddh' Hindi.
- Headings can be in Hinglish too (e.g. "## Main points", "## Kya karna hai").""",

    "Hindi": """Language: write everything in Hindi using the Devanagari script.
- Use simple, everyday Hindi that is easy to read.
- Common technical terms may stay in English in brackets the first time, e.g. प्रोटीन (protein).
- Write "Not mentioned in the video" as "वीडियो में इसका ज़िक्र नहीं है".""",
}


def build_system_prompt(style: str, language: str) -> str:
    """Glue the three prompt pieces together."""
    return f"{BASE_RULES}\n{STYLE_PROMPTS[style]}\n\n{LANGUAGE_PROMPTS[language]}"


# --- PDF prompts (same idea: rules + style + language) ---
PDF_BASE_RULES = """You are NoteTube, a careful note-taker who turns PDFs (books, study material, reports, slides) into notes a student can study from WITHOUT re-reading the PDF.

Strict rules:
- Use ONLY information in the PDF. Do not add facts, numbers, examples or explanations from your own knowledge.
- Be complete: cover EVERY chapter and section in this file, in the same order as the PDF. Never skip a section, even a short one.
- Also read tables, charts, diagrams, images and scanned pages.
- If the requested format asks for something the PDF does not contain, write "Not mentioned in the PDF" instead of guessing.
- Page references: write (p. N), where N counts pages inside THIS file (its first page is p. 1). Don't use page numbers printed on the pages.
- Write formulas in plain text on their own line, e.g. `Speed = Distance / Time`, and say what each symbol means. No LaTeX.
- Clean Markdown only: '#' headings, '-' bullets, '**bold**' key terms, Markdown tables. No HTML.
- Start directly with the notes. No greeting, no "Here are your notes", no closing remarks.
"""

PDF_STYLE_PROMPTS = {
    "Detailed study notes": """Format: DETAILED STUDY NOTES (complete enough to study from without the PDF)
- Follow the PDF's own structure: '## ' for each chapter (use the PDF's chapter title), '### ' for each section, '#### ' for sub-sections. Add (p. N) after each heading.
- Under every section, as bullets with sub-bullets for details:
  - the key points and explanations,
  - **Definitions:** '**Term** - meaning' exactly as the PDF defines it,
  - **Formulas:** each formula plus what every symbol means,
  - **Examples:** the examples and worked problems the PDF gives,
  - **Important numbers:** dates, values, statistics, quantities.
  Leave out a sub-heading when that section has nothing for it.
- Keep every table from the PDF as a Markdown table.
- Detail matters more than brevity. Don't compress several sections into one.""",

    "Exam revision": """Format: EXAM REVISION
For each chapter in this file, a '## ' heading with the chapter title and (p. N), then:
- '### Key concepts' - bullets, one concept each, bold the term, with (p. N).
- '### Formulas' - each formula and what the symbols mean, or "Not mentioned in the PDF".
- '### Likely exam questions' - 4 to 8 numbered questions, each followed by a short '**Answer:**' taken from the PDF.
- '### Common mistakes' - mistakes students are likely to make with THESE concepts (confusing two terms, wrong units, missed conditions). Base them on the PDF's content; don't add new facts.""",

    "Quick summary": """Format: QUICK SUMMARY
- One '## ' heading naming what this file is about.
- Then 8 to 12 bullets with the most important ideas, in the PDF's order, each with (p. N). Bold the key term in each bullet.
- End with one line: '**Bottom line:**' + the main message.""",

    "Flashcards (Anki CSV)": """Format: FLASHCARDS
Make question/answer flashcards that cover every section of this file, in order.
- About 1 to 3 cards per page of real content (skip covers, contents and index pages). At most 60 cards.
- Each question tests ONE fact, definition, formula or concept. Answers are short (one to three sentences).
- "page" is where the answer is found, counting pages inside THIS file (its first page is 1)."""
}

# Flashcards come back as JSON in this exact shape, so the CSV is always valid.
FLASHCARD_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "question": {"type": "STRING"},
            "answer": {"type": "STRING"},
            "page": {"type": "INTEGER"},
        },
        "required": ["question", "answer"],
    },
}


def build_pdf_system_prompt(style: str, language: str) -> str:
    language_rules = LANGUAGE_PROMPTS[language]
    if language == "Hindi":
        language_rules += '\n- Write "Not mentioned in the PDF" as "PDF में इसका ज़िक्र नहीं है".'
    return f"{PDF_BASE_RULES}\n{PDF_STYLE_PROMPTS[style]}\n\n{language_rules}"


def build_part_message(file_name, part, number, total, earlier_headings):
    """The message sent with each PDF part: where it sits in the whole PDF,
    and (for later parts) which headings already exist, so nothing repeats."""
    lines = [f'This file contains pages {part.start + 1} to {part.end + 1} of "{file_name}". '
             "For (p. N), count pages inside this file, starting at 1."]
    chapter_titles = [c.title for c in part.chapters if c.title != "Pages"]
    if chapter_titles:
        lines.append("Chapters in this file: " + "; ".join(chapter_titles))
    if total > 1:
        lines.append(f"This is part {number} of {total}. The parts' notes will be joined into one document.")
        if number > 1:
            lines.append("Notes for earlier parts already exist. Do NOT repeat them or re-introduce the document. "
                         "If this file starts in the middle of a section, continue it under the same heading "
                         "followed by '(continued)'. Use the same heading levels as before.")
            if earlier_headings:
                lines.append("Headings already used (last ones): " + " | ".join(earlier_headings[-30:]))
        if number < total:
            lines.append("More parts follow, so don't write a conclusion for the whole document.")
    lines.append("Now make the notes for this file, following the format and language rules.")
    return "\n".join(lines)


# Page references like "(p. 3)", "(p. 3-4)" or "(pp. 3-4)".
PAGE_REF = re.compile(r"\((pp?)\.\s*(\d+)(?:\s*[-–]\s*(\d+))?\)")


def to_pdf_page(n: int, part) -> int:
    """Gemini counts pages inside the part it was sent (p. 1 = the part's first
    page). Turn that into the real page number in the whole PDF."""
    pages_in_part = part.end - part.start + 1
    if 1 <= n <= pages_in_part:
        return n + part.start
    return n   # outside the part's range - probably already a real page number, keep it


def fix_page_refs(text: str, part) -> str:
    """Rewrite every (p. N) in the notes to real PDF page numbers."""
    def fix(match):
        first = to_pdf_page(int(match.group(2)), part)
        last = f"-{to_pdf_page(int(match.group(3)), part)}" if match.group(3) else ""
        return f"({match.group(1)}. {first}{last})"
    return PAGE_REF.sub(fix, text)


# ---------------------------------------------------------------------------
# 3. YOUTUBE HELPERS
# ---------------------------------------------------------------------------
# A YouTube video ID is always 11 characters: letters, digits, '-' or '_'.
VIDEO_ID_PATTERNS = [
    r"(?:youtube\.com|youtube-nocookie\.com)/(?:watch\?(?:.*&)?v=|shorts/|embed/|live/|v/)([A-Za-z0-9_-]{11})",
    r"youtu\.be/([A-Za-z0-9_-]{11})",
]


def extract_video_id(url: str):
    """Return the 11-character video ID from any common YouTube link, or None.

    Works for: youtube.com/watch?v=..., m.youtube.com/watch?v=..., youtu.be/...,
    youtube.com/shorts/..., /embed/... and /live/... links.
    """
    url = url.strip()
    for pattern in VIDEO_ID_PATTERNS:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def get_video_title(video_id: str) -> str:
    """Ask YouTube's free public 'oEmbed' service for the video title.

    No API key needed. If it fails for any reason we just use a generic title.
    """
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    oembed = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(watch_url)
    try:
        with urllib.request.urlopen(oembed, timeout=6) as response:
            return json.loads(response.read().decode("utf-8")).get("title") or "YouTube video"
    except Exception:
        return "YouTube video"


def fetch_transcript(video_id: str, language: str) -> str:
    """Download the video's captions and return them as text with [mm:ss] times.

    Order we try: requested language -> Hindi -> English (human-made captions
    first), then auto-generated captions in that same order, then anything.
    Raises a youtube_transcript_api error if there are no usable captions.
    """
    wanted = ["en"] if language == "English" else ["hi"]  # Hinglish videos use 'hi' captions
    for code in ["hi", "en"]:
        if code not in wanted:
            wanted.append(code)

    transcripts = list(YouTubeTranscriptApi().list(video_id))

    def find(code, generated):
        for t in transcripts:
            # 'en-US' and 'en-GB' both count as 'en'
            if t.is_generated == generated and t.language_code.split("-")[0] == code:
                return t
        return None

    chosen = None
    for generated in (False, True):  # human-made captions first, then auto-generated
        for code in wanted:
            chosen = chosen or find(code, generated)
    chosen = chosen or (transcripts[0] if transcripts else None)
    if chosen is None:
        raise yt_errors.NoTranscriptFound(video_id, wanted, None)

    # Join caption snippets into ~30 second chunks so the text is compact.
    lines, chunk, chunk_start = [], [], 0.0
    for snippet in chosen.fetch():
        if not chunk:
            chunk_start = snippet.start
        chunk.append(snippet.text.replace("\n", " "))
        if snippet.start - chunk_start >= 30:
            lines.append(f"[{format_time(chunk_start)}] {' '.join(chunk)}")
            chunk = []
    if chunk:
        lines.append(f"[{format_time(chunk_start)}] {' '.join(chunk)}")
    return "\n".join(lines)


def format_time(seconds: float) -> str:
    """75.3 -> '01:15', 3725 -> '1:02:05'."""
    seconds = int(seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def explain_transcript_error(err: Exception) -> str:
    """Turn a transcript library error into a plain-English reason."""
    if isinstance(err, (yt_errors.TranscriptsDisabled, yt_errors.NoTranscriptFound)):
        return "This video has no captions/subtitles we can read."
    if isinstance(err, (yt_errors.VideoUnavailable, yt_errors.VideoUnplayable)):
        return "The video is private, removed, or not playable."
    if isinstance(err, yt_errors.AgeRestricted):
        return "The video is age-restricted, so captions can't be read without signing in."
    if isinstance(err, (yt_errors.RequestBlocked, yt_errors.IpBlocked)):
        return ("YouTube blocked the caption request from this computer/server "
                "(this often happens on cloud hosting).")
    return f"Couldn't read captions ({type(err).__name__})."


# ---------------------------------------------------------------------------
# 4. GEMINI HELPERS
# ---------------------------------------------------------------------------
class QuotaError(Exception):
    """Raised when Gemini says 'too many requests' (error 429).

    kind is one of:
      "daily"  - the free requests-per-day limit is used up (on every model we tried)
      "minute" - too many requests this minute; waiting a minute fixes it
      "tokens" - this one request is bigger than the free tokens-per-minute
                 limit (usually a long video)
    """
    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


# Why ask_gemini skipped a model on its last call, e.g. ["gemini-3.8-flash: busy (503)"].
# Shown in the app so you can see why a backup model was used.
LAST_SKIPS = []


def retry_wait(err) -> float:
    """How long Google asks us to wait before retrying (it says e.g. 'retry in 17s')."""
    match = re.search(r"(?:retry in|retryDelay['\"]?:\s*['\"])\s*(\d+(?:\.\d+)?)s", str(err))
    return min(float(match.group(1)) + 1, 40) if match else 15


def quota_kind(err) -> str:
    """Read Google's 429 error text to see WHICH free limit was hit."""
    message = str(err)
    if "PerDay" in message:
        return "daily"
    if "Token" in message:
        return "tokens"
    return "minute"


def ask_gemini(api_key: str, contents, system_prompt: str, is_video: bool = False, json_schema=None):
    """Send a request to Gemini, trying each model until one works.

    Returns (notes_text, model_used). We move on to the next model when one
    is out of quota (429), not available (404) or temporarily overloaded (5xx).
    Any other error (e.g. bad API key) is raised right away.
    json_schema: ask for JSON in this exact shape instead of Markdown (used for flashcards).
    """
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=0.3,  # low = stick closely to the video, less "creative"
    )
    if is_video:
        # Low resolution uses about 1/3 of the tokens per second of video.
        # That's plenty for notes, and lets ~3x longer videos fit in the
        # free tokens-per-minute limit.
        config.media_resolution = types.MediaResolution.MEDIA_RESOLUTION_LOW
    if json_schema:
        config.response_mime_type = "application/json"
        config.response_schema = json_schema

    client = genai.Client(api_key=api_key)
    quota_kinds = []
    LAST_SKIPS.clear()

    for model in [MODEL_NAME] + FALLBACK_MODELS:
        # Up to 2 tries per model: if Google says "busy" or "too many requests
        # this minute", we wait and retry the SAME model once before moving on.
        # (Keeps all parts of a PDF written by the same model, in the same style.)
        for attempt in (1, 2):
            try:
                response = client.models.generate_content(model=model, contents=contents, config=config)
                if response.text and response.text.strip():
                    text = response.text.strip()
                    if response.candidates and response.candidates[0].finish_reason == types.FinishReason.MAX_TOKENS:
                        if json_schema:
                            raise RuntimeError("The answer was too long and got cut off. Try a smaller page range.")
                        text += ("\n\n> ⚠️ Gemini's answer hit its length limit here, so the notes for these "
                                 "pages may be cut short. Try a smaller page range for full detail.")
                    return text, model
                raise RuntimeError("Gemini returned an empty answer.")
            except genai_errors.ClientError as err:
                if err.code == 429:
                    kind = quota_kind(err)
                    if kind == "tokens":
                        # Too big for one request. Other models have the same
                        # limit, so don't waste requests - give up on this route.
                        raise QuotaError("tokens")
                    if kind == "minute" and attempt == 1:
                        time.sleep(retry_wait(err))   # wait for the minute to pass, then retry
                        continue
                    quota_kinds.append(kind)
                    LAST_SKIPS.append(f"{model}: {'daily' if kind == 'daily' else 'per-minute'} free limit reached")
                    break                    # try the next model (it has its own limits)
                if err.code == 404:          # model name retired / not available to this key
                    LAST_SKIPS.append(f"{model}: not available")
                    break
                raise
            except genai_errors.ServerError as err:   # Google's side is busy
                if attempt == 1:
                    time.sleep(5)
                    continue
                LAST_SKIPS.append(f"{model}: Google's servers busy ({err.code})")
                break

    if quota_kinds:
        # "daily" only if EVERY model we tried is out for the day.
        raise QuotaError("daily" if all(k == "daily" for k in quota_kinds) else "minute")
    raise RuntimeError("None of the Gemini models in the list are available right now. "
                       "Check MODEL_NAME at the top of app.py.")


def explain_gemini_error(err: Exception, thing: str = "video") -> str:
    """Turn a Gemini error into a plain-English reason. thing = "video" or "PDF part"."""
    if isinstance(err, QuotaError):
        if err.kind == "daily":
            return ("The free daily limit for Gemini may be reached (all free models tried). "
                    "Try again tomorrow.")
        if err.kind == "tokens":
            return (f"The {thing} is too long for the free per-minute token limit, "
                    "so Gemini couldn't read it in one go.")
        return "Too many requests in the last minute (free limit). Wait about a minute and try again."
    message = str(err)
    if isinstance(err, genai_errors.ClientError):
        if err.code in (401, 403) or "API key" in message or "API_KEY" in message:
            return "Your Gemini API key was rejected. Check it in the sidebar."
        if "token" in message.lower() and ("exceed" in message.lower() or "limit" in message.lower()):
            return f"The {thing} is too long for the free model to process in one go."
        if "private" in message.lower() or "unavailable" in message.lower():
            return "Gemini can't open the video - it may be private, unlisted or region-blocked."
    return f"Gemini couldn't read the {thing} ({message[:200]})."


def notes_from_url(api_key, video_id, style, language):
    """Method A: let Gemini watch the YouTube video itself."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    contents = types.Content(role="user", parts=[
        types.Part(file_data=types.FileData(file_uri=url)),
        types.Part(text=f"Make {style.lower()} notes for this video, following the format and language rules."),
    ])
    return ask_gemini(api_key, contents, build_system_prompt(style, language), is_video=True)


def notes_from_pdf_part(api_key, pdf_bytes, message, style, language):
    """Send one PDF part to Gemini. Gemini reads it page by page, as images
    plus text, so tables, diagrams and scanned pages all work."""
    contents = [types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"), message]
    is_flashcards = style.startswith("Flashcards")
    return ask_gemini(api_key, contents, build_pdf_system_prompt(style, language),
                      json_schema=FLASHCARD_SCHEMA if is_flashcards else None)


def notes_from_text(api_key, transcript, style, language):
    """Methods B and C: send transcript text to Gemini."""
    prompt = (f"Below is the transcript of a YouTube video. Make {style.lower()} notes from it, "
              f"following the format and language rules.\n\nTRANSCRIPT:\n{transcript}")
    return ask_gemini(api_key, prompt, build_system_prompt(style, language))


# ---------------------------------------------------------------------------
# 5. SMALL UI HELPERS
# ---------------------------------------------------------------------------
def get_api_key():
    """Key typed in the sidebar wins; otherwise use the one in Streamlit secrets."""
    typed = st.session_state.get("api_key_input", "").strip()
    if typed and typed != PLACEHOLDER_KEY:
        return typed
    try:
        secret = str(st.secrets["GEMINI_API_KEY"]).strip()
    except Exception:  # no secrets.toml file, or no key inside it
        return None
    return secret if secret and secret != PLACEHOLDER_KEY else None


def copy_button(text: str):
    """A 'Copy notes' button. Streamlit has no built-in one, so we use a tiny bit of HTML/JS."""
    # json.dumps turns the notes into a JavaScript string. Escaping "<" stops
    # text like "</script>" in the notes from breaking out of the script.
    safe_text = json.dumps(text).replace("<", "\\u003c")
    st.iframe(f"""
        <button id="copy" style="width:100%;min-height:46px;font-size:16px;border-radius:8px;
            border:1px solid #8884;background:#ff4b4b;color:white;cursor:pointer;">📋 Copy notes</button>
        <script>
        const btn = document.getElementById("copy");
        btn.onclick = async () => {{
            const text = {safe_text};
            try {{ await navigator.clipboard.writeText(text); }}
            catch (e) {{  // older browsers: copy through a hidden text box
                const box = document.createElement("textarea");
                box.value = text; document.body.appendChild(box); box.select();
                document.execCommand("copy"); box.remove();
            }}
            btn.innerText = "✅ Copied!";
            setTimeout(() => btn.innerText = "📋 Copy notes", 2000);
        }};
        </script>""", height=56)


@st.cache_data(show_spinner=False)
def make_pdf(notes, title, url, created, subtitle):
    """Cached so the PDF is only built once per note, not on every click."""
    return markdown_to_pdf(notes, title=title, video_url=url, date_text=created, subtitle=subtitle)


def save_to_history(entry: dict):
    st.session_state.history.append(entry)
    st.session_state.current = len(st.session_state.history) - 1


def find_in_history(key: str, style: str, language: str):
    """Return the position of notes we already made for this exact video (or
    PDF + page range), style and language - so we can show them again without
    a new request. YouTube notes are matched by their URL, PDF notes by "key"."""
    for i, item in enumerate(st.session_state.history):
        if (item.get("key", item["url"]), item["style"], item["language"]) == (key, style, language):
            return i
    return None


# ---------------------------------------------------------------------------
# 6. PAGE SETUP AND STYLE
# ---------------------------------------------------------------------------
st.set_page_config(page_title="NoteTube", page_icon="📝", layout="centered")

# Bigger tap targets and fonts for phones. Colours come from Streamlit's
# theme, so this works in both dark and light mode.
st.markdown("""
<style>
.stButton button, .stDownloadButton button { min-height: 48px; font-size: 1rem; width: 100%; }
.stTextInput input, .stTextArea textarea { font-size: 16px; }   /* 16px stops iPhone zooming in */
div[data-baseweb="select"] > div { min-height: 48px; }
.block-container { padding-top: 2rem; padding-bottom: 3rem; }
</style>
""", unsafe_allow_html=True)

# st.session_state is memory that survives button clicks during your visit.
st.session_state.setdefault("history", [])      # list of all notes made this session
st.session_state.setdefault("current", None)    # which history item is shown
st.session_state.setdefault("failure", None)    # reasons, when both methods failed

# ---------------------------------------------------------------------------
# 7. SIDEBAR: API key, usage note, history
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Settings")
    st.text_input("Gemini API key", type="password", key="api_key_input",
                  placeholder="Paste your free key here",
                  help="Get a free key at aistudio.google.com. It's kept only for this browser session.")
    if not st.session_state.api_key_input and get_api_key():
        st.caption("✅ Using the key saved in the app's secrets.")
    st.info("ℹ️ Free Gemini usage is limited per day (about 20 notes on the main model). "
            "When it runs out, NoteTube switches to other free models automatically. "
            "Re-opening notes from History is free.")
    # The History list is drawn at the very end of this file (section 11),
    # so notes made during this run show up in it straight away.

# ---------------------------------------------------------------------------
# 8. TITLE + TABS, then the YOUTUBE TAB
# ---------------------------------------------------------------------------
st.title("📝 NoteTube")
st.caption("Turn a YouTube video or a PDF into clean notes in English, Hinglish or Hindi.")

tab_youtube, tab_pdf = st.tabs(["▶️ YouTube", "📄 PDF"])

with tab_youtube:
    url = st.text_input("YouTube link", placeholder="https://youtu.be/...")
    language = st.selectbox("Language", LANGUAGES)
    style = st.selectbox("Note style", NOTE_STYLES)
    make_clicked = st.button("✨ Make notes", type="primary", use_container_width=True)

    if make_clicked:
        st.session_state.failure = None
        api_key = get_api_key()
        video_id = extract_video_id(url)

        if not api_key:
            st.error("Please paste your free Gemini API key in the sidebar (☰ menu on phones).")
        elif not video_id:
            st.error("That doesn't look like a YouTube link. Try one like https://youtu.be/abc123XYZ_0 "
                     "or https://www.youtube.com/watch?v=abc123XYZ_0")
        elif (earlier := find_in_history(f"https://www.youtube.com/watch?v={video_id}", style, language)) is not None:
            # Same video + style + language as before: reuse it, no new request.
            st.session_state.current = earlier
            st.info("You already made these notes this session - showing them again (no free request used).")
        else:
            reasons = []
            notes = model = source = None
            skip_captions = False

            # Method A: Gemini watches the video directly.
            with st.spinner("Gemini is watching the video... (this can take up to a minute)"):
                try:
                    notes, model = notes_from_url(api_key, video_id, style, language)
                    source = "video"
                except Exception as err:
                    # Out of daily/minute quota -> the captions route would hit the
                    # same limit, so skip it. A "too long" video is fine to retry
                    # with captions: text uses far fewer tokens than video.
                    skip_captions = isinstance(err, QuotaError) and err.kind != "tokens"
                    reasons.append("Direct video: " + explain_gemini_error(err))

            # Method B: read the captions and send the text.
            if notes is None and not skip_captions:
                with st.spinner("Trying the video's captions instead..."):
                    try:
                        transcript = fetch_transcript(video_id, language)
                    except Exception as err:
                        reasons.append("Captions: " + explain_transcript_error(err))
                    else:
                        try:
                            notes, model = notes_from_text(api_key, transcript, style, language)
                            source = "captions"
                        except Exception as err:
                            reasons.append("Captions: " + explain_gemini_error(err))

            if notes:
                save_to_history({
                    "title": get_video_title(video_id), "url": f"https://www.youtube.com/watch?v={video_id}",
                    "language": language, "style": style, "notes": notes, "model": model, "source": source,
                    "created": datetime.now().strftime("%d %b %Y, %I:%M %p"),
                })
            else:
                # Method C: tell the user why, and open the paste box below.
                st.session_state.failure = {"reasons": reasons, "video_id": video_id}

    # Manual transcript (shown open if both automatic methods failed)
    failure = st.session_state.failure
    if failure:
        st.error("😕 Couldn't make notes automatically:\n\n" + "\n".join(f"- {r}" for r in failure["reasons"]))

    with st.expander("📄 Paste a transcript yourself", expanded=bool(failure)):
        st.caption("On YouTube: open the video → ⋯ More → Show transcript → select all the text and copy it.")
        pasted = st.text_area("Transcript", height=200, label_visibility="collapsed",
                              placeholder="Paste the video's transcript here...")
        if st.button("Make notes from this transcript", use_container_width=True):
            api_key = get_api_key()
            if not api_key:
                st.error("Please paste your free Gemini API key in the sidebar.")
            elif len(pasted.strip()) < 50:
                st.error("Please paste a longer transcript (at least a few sentences).")
            else:
                with st.spinner("Writing your notes..."):
                    try:
                        notes, model = notes_from_text(api_key, pasted, style, language)
                    except Exception as err:
                        st.error(explain_gemini_error(err))
                    else:
                        vid = extract_video_id(url)
                        st.session_state.failure = None
                        save_to_history({
                            "title": get_video_title(vid) if vid else "Pasted transcript",
                            "url": f"https://www.youtube.com/watch?v={vid}" if vid else "",
                            "language": language, "style": style, "notes": notes, "model": model,
                            "source": "pasted transcript",
                            "created": datetime.now().strftime("%d %b %Y, %I:%M %p"),
                        })
                        st.rerun()

# ---------------------------------------------------------------------------
# 9. PDF TAB
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner=False, max_entries=3)
def load_pdf(file_id: str, _data: bytes):
    """Open the PDF once per file. Cached by the file's fingerprint (file_id);
    the leading _ tells Streamlit not to re-scan the (maybe 200 MB) bytes."""
    return open_pdf(_data)


@st.cache_data(show_spinner=False, max_entries=20)
def get_plan(file_id: str, _reader, file_size: int, first: int, last: int, max_pages: int):
    """Work out the parts once per file + page range + style (cached)."""
    return plan_parts(_reader, file_size, first, last, max_pages)


def cards_to_markdown(cards: list) -> str:
    lines = [f"## Flashcards ({len(cards)})", ""]
    for i, card in enumerate(cards, 1):
        page = f" (p. {card['page']})" if card.get("page") else ""
        lines += [f"**Q{i}. {card['question']}**", "", f"{card['answer']}{page}", ""]
    return "\n".join(lines)


def cards_to_csv(cards: list) -> str:
    """Two columns (front, back) - the format Anki imports. The csv module
    adds quotes where needed, so commas and line breaks in cards are safe."""
    out = io.StringIO()
    writer = csv.writer(out)
    for card in cards:
        page = f" (p. {card['page']})" if card.get("page") else ""
        writer.writerow([card["question"], f"{card['answer']}{page}"])
    return out.getvalue()


SPLIT_METHOD_TEXT = {
    "bookmarks": "📑 Split by chapter, using the PDF's bookmarks.",
    "headings": "🔎 No bookmarks, so chapters were found from headings in the text.",
    "pages": "📄 No chapters found, so the PDF is split by pages.",
}

with tab_pdf:
    uploaded = st.file_uploader("Upload a PDF", type=["pdf"],
                                help=f"Up to {MAX_UPLOAD_MB} MB and 1,000 pages. Books, notes, reports, slides "
                                     "and scanned pages all work.")
    st.caption(f"Max {MAX_UPLOAD_MB} MB, 1,000 pages. Big PDFs are split into parts by chapter automatically.")

    reader = None
    if uploaded is not None:
        data = uploaded.getvalue()
        file_id = hashlib.sha1(data).hexdigest()[:16]   # fingerprint: same file -> same id
        try:
            reader = load_pdf(file_id, data)
        except ValueError as err:
            st.error(str(err))

    if reader is not None and len(reader.pages) > 1000:
        st.error(f"This PDF has {len(reader.pages)} pages. Gemini reads at most 1,000 pages per file, "
                 "so choose a page range of up to 1,000 pages below.")

    if reader is not None:
        page_count = len(reader.pages)
        st.success(f"📄 **{uploaded.name}** · {page_count} pages · {len(data) / 1e6:.1f} MB")

        # Page range - e.g. one chapter at a time. The keys include file_id,
        # so a new file starts with a fresh range.
        col_from, col_to = st.columns(2)
        first_page = col_from.number_input("From page", 1, page_count, 1, key=f"pdf_from_{file_id}")
        last_page = col_to.number_input("To page", 1, page_count, page_count, key=f"pdf_to_{file_id}")
        pdf_language = st.selectbox("Language", LANGUAGES, key="pdf_language")
        pdf_style = st.selectbox("Note style", list(PDF_STYLES), key="pdf_style")

        if first_page > last_page:
            st.error("'From page' must be smaller than or equal to 'To page'.")
        else:
            first, last = int(first_page) - 1, int(last_page) - 1   # pypdf counts pages from 0
            with st.spinner("Finding chapters..."):
                method, parts = get_plan(file_id, reader, len(data), first, last, PDF_STYLES[pdf_style])

            # Show the plan BEFORE any requests are used.
            st.caption(SPLIT_METHOD_TEXT[method])
            plural = "s" if len(parts) > 1 else ""
            with st.expander(f"🧩 {len(parts)} part{plural} - see them, or download each part as its own PDF",
                             expanded=len(parts) <= 6):
                base = re.sub(r"[^A-Za-z0-9]+", "_", uploaded.name.rsplit(".", 1)[0]).strip("_")[:40] or "pdf"
                for n, part in enumerate(parts, 1):
                    col_text, col_button = st.columns([3, 1])
                    titles = "; ".join(c.title for c in part.chapters if c.title != "Pages")
                    size = f"~{part.est_bytes / 1e6:.1f} MB" if part.est_bytes >= 100_000 else "<0.1 MB"
                    col_text.markdown(f"**Part {n}:** {part.label} · {size}")
                    if titles:
                        col_text.caption(titles[:150])
                    # The part's PDF is only built when you click (we pass a function,
                    # not the bytes), so big files don't fill the memory up front.
                    col_button.download_button(
                        "⬇️ PDF", data=partial(part_pdf_bytes, reader, part.start, part.end),
                        file_name=f"{base}_part{n}_p{part.start + 1}-{part.end + 1}.pdf",
                        mime="application/pdf", key=f"part_dl_{file_id}_{first}_{last}_{pdf_style}_{n}",
                        use_container_width=True)

            requests_needed = len(parts)
            usage = (f"This will use **{requests_needed} Gemini request{plural}** "
                     f"(you get about {FREE_REQUESTS_PER_DAY} free per day, shared with YouTube).")
            if requests_needed >= FREE_REQUESTS_PER_DAY:
                st.warning(usage + " That's a whole day's free limit (or more) - choose a smaller page range "
                           "(a few chapters at a time) and do the rest tomorrow.")
            elif requests_needed > FREE_REQUESTS_PER_DAY // 2:
                st.warning(usage)
            else:
                st.info(usage)

            if st.button("✨ Start", type="primary", use_container_width=True, key="pdf_start"):
                api_key = get_api_key()
                history_key = f"pdf:{file_id}:{first}-{last}"
                earlier = find_in_history(history_key, pdf_style, pdf_language)
                if not api_key:
                    st.error("Please paste your free Gemini API key in the sidebar (☰ menu on phones).")
                elif earlier is not None:
                    st.session_state.current = earlier
                    st.info("You already made these notes this session - showing them again "
                            "(no free request used).")
                else:
                    is_flashcards = pdf_style.startswith("Flashcards")
                    texts, cards, headings, models_used, skips = [], [], [], [], []
                    stopped = None   # (part number, error) if something went wrong
                    progress = st.progress(0.0)

                    # Send the parts one after another.
                    for n, part in enumerate(parts, 1):
                        if n > 1:
                            progress.progress((n - 1) / len(parts),
                                              text=f"Short pause before part {n} (free limit: 5 requests/minute)...")
                            time.sleep(PAUSE_BETWEEN_PARTS)
                        progress.progress((n - 1) / len(parts),
                                          text=f"Part {n} of {len(parts)}: {part.label} - Gemini is reading...")
                        message = build_part_message(uploaded.name, part, n, len(parts), headings)
                        try:
                            text, model = notes_from_pdf_part(
                                api_key, part_pdf_bytes(reader, part.start, part.end),
                                message, pdf_style, pdf_language)
                            if is_flashcards:
                                for card in json.loads(text):
                                    if card.get("question") and card.get("answer"):
                                        if card.get("page"):
                                            card["page"] = to_pdf_page(int(card["page"]), part)
                                        cards.append(card)
                            else:
                                text = fix_page_refs(text, part)   # page numbers -> real PDF pages
                                texts.append(text)
                                # Remember the headings so the next part doesn't repeat them.
                                headings += [PAGE_REF.sub("", ln).strip() for ln in text.splitlines()
                                             if ln.startswith("#")]
                            skips += [f"Part {n}: {s}" for s in LAST_SKIPS]
                        except Exception as err:
                            stopped = (n, err)
                            break
                        models_used.append(model)
                    progress.progress(1.0, text="Stopped." if stopped else "Done!")
                    if skips:   # explain why a backup model was used for some parts
                        st.caption("ℹ️ Backup model used: " + "; ".join(skips))

                    if stopped:
                        n, err = stopped
                        if isinstance(err, json.JSONDecodeError):
                            reason = "Gemini's flashcards came back in a broken format. Try again."
                        else:
                            reason = explain_gemini_error(err, thing="PDF part")
                        st.error(f"Part {n} of {len(parts)} failed: {reason}")

                    if models_used:   # at least one part worked -> keep it
                        notes = cards_to_markdown(cards) if is_flashcards else "\n\n".join(texts)
                        done_to = parts[len(models_used) - 1].end
                        if stopped:
                            # Keep what we have, and say exactly what's left to do.
                            notes += (f"\n\n> ⚠️ Stopped after part {len(models_used)} of {len(parts)}. "
                                      f"Not done yet: pages {done_to + 2}-{last + 1}. To finish, run "
                                      f"From page {done_to + 2} To page {last + 1} later.")
                        save_to_history({
                            "title": uploaded.name, "url": "",
                            # Partial notes get a different key, so pressing Start again really retries.
                            "key": history_key + (":partial" if stopped else ""),
                            "language": pdf_language, "style": pdf_style, "notes": notes,
                            "model": ", ".join(dict.fromkeys(models_used)),   # each model once
                            "source": f"PDF pages {first + 1}-{done_to + 1}",
                            "created": datetime.now().strftime("%d %b %Y, %I:%M %p"),
                            "cards": cards if is_flashcards else None,
                        })

# ---------------------------------------------------------------------------
# 10. SHOW THE NOTES + COPY / DOWNLOAD BUTTONS
# ---------------------------------------------------------------------------
if st.session_state.current is not None:
    item = st.session_state.history[st.session_state.current]
    st.divider()
    st.subheader(item["title"])
    st.caption(f"{item['style']} · {item['language']} · made from {item['source']} "
               f"with {item['model']} · {item['created']}")

    with st.container(border=True):
        st.markdown(item["notes"])

    # A safe file name: keep letters/numbers, replace the rest with '_'
    file_base = re.sub(r"[^A-Za-z0-9]+", "_", item["title"]).strip("_")[:50] or "notes"
    link = f"{item['url']}\n\n" if item["url"] else ""   # PDF notes have no link
    md_text = f"# {item['title']}\n\n{link}{item['notes']}\n"

    copy_button(item["notes"])
    col1, col2, col3 = st.columns(3)  # side by side on laptops, stacked on phones
    col1.download_button("⬇️ .md", md_text, file_name=f"{file_base}.md",
                         mime="text/markdown", use_container_width=True)
    col2.download_button("⬇️ .txt", markdown_to_plain_text(md_text), file_name=f"{file_base}.txt",
                         mime="text/plain", use_container_width=True)
    try:
        pdf_bytes = make_pdf(item["notes"], item["title"], item["url"], item["created"],
                             f"{item['style']} · {item['language']}")
        col3.download_button("⬇️ PDF", pdf_bytes, file_name=f"{file_base}.pdf",
                             mime="application/pdf", use_container_width=True)
    except Exception as err:
        col3.warning(f"PDF unavailable: {err}")
    if item.get("cards"):   # flashcards -> also a CSV file for Anki
        st.download_button("⬇️ Flashcards .csv (for Anki)", cards_to_csv(item["cards"]),
                           file_name=f"{file_base}_flashcards.csv", mime="text/csv",
                           use_container_width=True)

# ---------------------------------------------------------------------------
# 11. SIDEBAR HISTORY - drawn last, so notes made in this run appear at once.
#     The buttons use on_click, which runs BEFORE the page is redrawn, so the
#     chosen notes show up immediately in the area above.
# ---------------------------------------------------------------------------
def open_from_history(i):
    st.session_state.current = i


def clear_history():
    st.session_state.history, st.session_state.current = [], None


with st.sidebar:
    st.header("🕘 History")
    if not st.session_state.history:
        st.caption("Notes you make will appear here.")
    for i in reversed(range(len(st.session_state.history))):
        item = st.session_state.history[i]
        label = f"{item['title'][:40]} · {item['style'].split()[0]} · {item['language']}"
        st.button(label, key=f"hist_{i}", use_container_width=True, on_click=open_from_history, args=(i,))
    if st.session_state.history:
        st.button("Clear history", use_container_width=True, on_click=clear_history)
