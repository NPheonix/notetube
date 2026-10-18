Build a free web app called "NoteTube" that turns a YouTube link into clean notes. I'm a beginner, so keep the code simple, well commented, and explain each step as you go.

GOAL
I paste a YouTube link, pick a language and a note style, and get notes I can read, copy and download. It must work well on mobile, tablet and laptop browsers, and be easy to add to a phone home screen.

HARD CONSTRAINTS
- Everything must be free: free Gemini API key, free hosting. No paid services.
- Never hardcode or commit an API key. The user pastes their own Gemini key in a sidebar field (kept only in session memory), and the app also reads st.secrets["GEMINI_API_KEY"] if it exists. Add a .gitignore that excludes secrets.toml, .env and venvs.
- Python + Streamlit (deployable to Streamlit Community Cloud from GitHub).

FEATURES
1. Input: YouTube URL (accept youtube.com/watch, youtu.be, shorts and mobile links; validate and show a friendly error otherwise).
2. Language dropdown: English, Hinglish (Hindi written in Roman/English letters, natural conversational style), Hindi (Devanagari).
3. Note style dropdown:
   - Quick summary (5-8 bullets)
   - Detailed study notes (headings, key points, definitions, examples, timestamps where available)
   - Action items and takeaways
   - Content ideas for a fitness coaching brand (CPV FitLab): 5 Instagram carousel ideas plus 3 Reels hooks based on the video
4. Output rendered as Markdown, with a Copy button and a Download as .md button (and .txt).
5. Session history in the sidebar so I can reopen earlier notes during the session.
6. Progress spinner and clear error messages.

HOW IT READS THE VIDEO (in this order)
a. Primary: send the YouTube URL directly to Gemini using the google-genai SDK (file_data / file_uri with the YouTube URL).
b. Fallback: if that fails, fetch the transcript with youtube-transcript-api (try the requested language, then Hindi, then English, then auto-generated) and send the text to Gemini.
c. If both fail, tell me plainly why (private video, no captions, blocked, too long) and let me paste a transcript manually into a text box.

MODEL AND QUOTA HANDLING
- Keep the model name in one constant at the top and make it easy to change. Use a current free-tier Gemini Flash model; check the Gemini API docs for the current model name rather than guessing.
- Handle 429/quota errors gracefully: show a message saying the free daily limit may be reached, and try the next model in a fallback list.
- Show a small note in the UI that free usage is limited per day.

PROMPT QUALITY
- Write good system prompts for each note style. For Hinglish, tell the model to write in natural Roman-script Hindi mixed with English terms, the way people actually text. Instruct it not to invent facts that are not in the video, and to say "not mentioned in the video" where needed.

UI
- Clean, mobile-first layout, large tap targets, dark and light friendly. Title, short tagline, and the controls in a simple vertical flow.

DELIVERABLES
- app.py, requirements.txt, README.md with: how to get a free Gemini key (aistudio.google.com), run locally (commands for Windows), push to GitHub, deploy free on Streamlit Community Cloud, add the key in its Secrets settings, and add to phone home screen.
- After building, run it locally and test with a short public YouTube video in English, then Hinglish. Fix any errors you find, and tell me exactly what to do to run it.

Ask me before installing anything beyond Python packages.
