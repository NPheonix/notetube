"""
NoteTube - turn a YouTube link into clean notes.

How the app works (big picture):
  1. You paste a YouTube link and pick a language + note style.
  2. We ask Google's Gemini AI to "watch" the video directly from its URL.
  3. If that fails, we download the video's captions (transcript) and send
     that text to Gemini instead.
  4. If that also fails, you can paste a transcript yourself.
  5. The notes are shown on screen and you can copy or download them.

Run it with:   streamlit run app.py
"""

import json
import re
import urllib.parse
import urllib.request
from datetime import datetime

import streamlit as st
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api import _errors as yt_errors

from export_utils import markdown_to_pdf, markdown_to_plain_text

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


def quota_kind(err) -> str:
    """Read Google's 429 error text to see WHICH free limit was hit."""
    message = str(err)
    if "PerDay" in message:
        return "daily"
    if "Token" in message:
        return "tokens"
    return "minute"


def ask_gemini(api_key: str, contents, system_prompt: str, is_video: bool = False):
    """Send a request to Gemini, trying each model until one works.

    Returns (notes_text, model_used). We move on to the next model when one
    is out of quota (429), not available (404) or temporarily overloaded (5xx).
    Any other error (e.g. bad API key) is raised right away.
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

    client = genai.Client(api_key=api_key)
    quota_kinds = []

    for model in [MODEL_NAME] + FALLBACK_MODELS:
        try:
            response = client.models.generate_content(model=model, contents=contents, config=config)
            if response.text and response.text.strip():
                return response.text.strip(), model
            raise RuntimeError("Gemini returned an empty answer.")
        except genai_errors.ClientError as err:
            if err.code == 429:
                kind = quota_kind(err)
                if kind == "tokens":
                    # Too big for one request. Other models have the same
                    # limit, so don't waste requests - give up on this route.
                    raise QuotaError("tokens")
                quota_kinds.append(kind)
                continue                 # try the next model (it has its own limits)
            if err.code == 404:          # model name retired / not available to this key
                continue
            raise
        except genai_errors.ServerError:  # Google's side is busy - try the next model
            continue

    if quota_kinds:
        # "daily" only if EVERY model we tried is out for the day.
        raise QuotaError("daily" if all(k == "daily" for k in quota_kinds) else "minute")
    raise RuntimeError("None of the Gemini models in the list are available right now. "
                       "Check MODEL_NAME at the top of app.py.")


def explain_gemini_error(err: Exception) -> str:
    """Turn a Gemini error into a plain-English reason."""
    if isinstance(err, QuotaError):
        if err.kind == "daily":
            return ("The free daily limit for Gemini may be reached (all free models tried). "
                    "Try again tomorrow.")
        if err.kind == "tokens":
            return ("The video is too long for the free per-minute token limit, "
                    "so Gemini couldn't watch it in one go.")
        return "Too many requests in the last minute (free limit). Wait about a minute and try again."
    message = str(err)
    if isinstance(err, genai_errors.ClientError):
        if err.code in (401, 403) or "API key" in message or "API_KEY" in message:
            return "Your Gemini API key was rejected. Check it in the sidebar."
        if "token" in message.lower() and ("exceed" in message.lower() or "limit" in message.lower()):
            return "The video is too long for the free model to process in one go."
        if "private" in message.lower() or "unavailable" in message.lower():
            return "Gemini can't open the video - it may be private, unlisted or region-blocked."
    return f"Gemini couldn't read the video directly ({message[:200]})."


def notes_from_url(api_key, video_id, style, language):
    """Method A: let Gemini watch the YouTube video itself."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    contents = types.Content(role="user", parts=[
        types.Part(file_data=types.FileData(file_uri=url)),
        types.Part(text=f"Make {style.lower()} notes for this video, following the format and language rules."),
    ])
    return ask_gemini(api_key, contents, build_system_prompt(style, language), is_video=True)


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


def find_in_history(video_url: str, style: str, language: str):
    """Return the position of notes we already made for this exact video,
    style and language - so we can show them again without a new request."""
    for i, item in enumerate(st.session_state.history):
        if (item["url"], item["style"], item["language"]) == (video_url, style, language):
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

    st.header("🕘 History")
    if not st.session_state.history:
        st.caption("Notes you make will appear here.")
    for i in reversed(range(len(st.session_state.history))):
        item = st.session_state.history[i]
        label = f"{item['title'][:40]} · {item['style'].split()[0]} · {item['language']}"
        if st.button(label, key=f"hist_{i}", use_container_width=True):
            st.session_state.current = i
    if st.session_state.history and st.button("Clear history", use_container_width=True):
        st.session_state.history, st.session_state.current = [], None
        st.rerun()

# ---------------------------------------------------------------------------
# 8. MAIN FORM
# ---------------------------------------------------------------------------
st.title("📝 NoteTube")
st.caption("Paste a YouTube link. Get clean notes in English, Hinglish or Hindi.")

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

# ---------------------------------------------------------------------------
# 9. MANUAL TRANSCRIPT (shown open if both automatic methods failed)
# ---------------------------------------------------------------------------
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
    md_text = f"# {item['title']}\n\n{item['url']}\n\n{item['notes']}\n"

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
