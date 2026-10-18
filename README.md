# 📝 NoteTube

Paste a YouTube link **or upload a PDF** → get clean notes in **English, Hinglish or Hindi**.
Copy them, or download as **.md, .txt or PDF** (flashcards also as **CSV for Anki**). Works on phone, tablet and laptop. 100% free.

**YouTube note styles:** Quick summary · Detailed study notes · Action items and takeaways · Content ideas for CPV FitLab (5 Instagram carousels + 3 Reels hooks)
**PDF note styles:** Detailed study notes · Exam revision · Quick summary · Flashcards (Anki CSV)

---

## How it reads a video (YouTube tab)

1. **Gemini watches the video directly** from its YouTube URL (public videos only).
2. If that fails → it downloads the video's **captions** and sends the text to Gemini.
3. If that fails too → it tells you why, and you can **paste a transcript** yourself
   (on YouTube: ⋯ More → *Show transcript* → copy the text).

## PDF notes (PDF tab)

Upload a book chapter, study material, report or slides and get **full study notes** - detailed
enough to study from without re-reading the PDF. Gemini reads every page as an image plus text,
so **tables, diagrams and scanned pages** work too.

1. **Upload** a PDF - up to **200 MB** and **1,000 pages**. NoteTube shows the page count.
2. **Pick a page range** (optional) - e.g. *From page 10 To page 35* to do one chapter at a time.
3. Pick the **language** and **note style**:

   | Style | What you get | Pages per request |
   |---|---|---|
   | Detailed study notes (default) | Follows the PDF's chapters and sections: key points, definitions, formulas, examples, important numbers, tables, page refs like (p. 12) | 20 |
   | Exam revision | Per chapter: key concepts, formulas, likely questions with short answers, common mistakes | 30 |
   | Quick summary | 8-12 bullets | 150 |
   | Flashcards (Anki CSV) | Question/answer cards, downloadable as CSV | 25 |

4. **Check the plan before you start.** Big PDFs are split into parts, and NoteTube shows them first,
   e.g. *"Part 1: Chapters 1-4 (pages 1-62)"*, and tells you **how many requests** it will use.
   Nothing is sent to Gemini until you press **Start**.
5. Press **Start**. You'll see *"Part 2 of 4..."* while it works, with a short pause between parts
   (the free tier allows 5 requests per minute). The parts' notes are joined into one document.

### How big PDFs are split (smart splitting)

- NoteTube uses the PDF's **bookmarks** (the chapter list in your PDF viewer's sidebar) to split by chapter.
  Each part holds only **complete chapters**; a chapter that doesn't fit starts the next part.
- Only a chapter that's too big on its own is split by pages (into even pieces, e.g. 15 + 15 + 15 pages).
- No bookmarks? It looks for headings like *"Chapter 3"* / *"Unit 2"* / *"अध्याय 3"* in the page text.
  If it can't find any (e.g. scanned books), it splits by pages.
- Each part stays under **20 MB**. Pages are copied as they are - **no image compression**, so scanned
  pages stay readable.
- You can **download each part as its own PDF** from the parts list.

### Saving your free requests

- **1 part = 1 request.** PDFs share the free daily limits with YouTube (see *Choosing a model* below).
- A 300-page book in detailed notes is about 15-20 requests. In **Auto**, that runs on 3.5 Flash Lite (~500/day);
  on 3.8 Flash (~20/day) do **a few chapters per day** with the page range.
- If the free limit runs out halfway, the parts already done are **kept**, and the notes say exactly
  which pages are left (e.g. *"run From page 63 To page 120 later"*).
- Pressing Start again for the same PDF + pages + style + language re-opens the notes for free.

### Flashcards → Anki

1. Choose **Flashcards (Anki CSV)** and press Start, then **⬇️ Flashcards .csv (for Anki)**.
2. In Anki (desktop): **File → Import** → pick the CSV → *Field separator:* **Comma** → *Note type:* **Basic** →
   Field 1 = Front, Field 2 = Back → **Import**.

## Files

| File | What it is |
|---|---|
| `app.py` | The whole app (UI, prompts, Gemini, YouTube and PDF logic) |
| `pdf_tools.py` | Opens PDFs, finds chapters and splits big PDFs into parts |
| `export_utils.py` | Turns notes into .txt and PDF |
| `fonts/` | Free Noto fonts so the PDF shows Hindi correctly (OFL licence) |
| `requirements.txt` | Python packages the app needs |
| `.streamlit/config.toml` | Theme settings (safe to share) |
| `.streamlit/secrets.toml` | **Your API key - local only, never uploaded** |
| `.gitignore` | Stops secrets and the `venv` folder from going to GitHub |

---

## 1. Get a free Gemini API key

1. Go to **https://aistudio.google.com/apikey** and sign in with a Google account.
2. Click **Create API key** → copy it (it starts with `AIza...`).
3. Treat it like a password: don't share it or put it in your code.

### Choosing a model (sidebar → Model)

| Choice | Model | Free limit | Use it for |
|---|---|---|---|
| **Auto (recommended)** | picks per task (below) | - | Most of the time |
| Best quality | Gemini 3.8 Flash | ~20 requests/day, 5/minute | Important notes where quality matters most |
| More requests | Gemini 3.5 Flash Lite | ~500 requests/day, 15/minute | Lots of notes, big PDFs |

**What Auto picks:** 3.8 Flash for YouTube and for detailed notes / exam revision on PDFs with up to 3 parts;
3.5 Flash Lite for flashcards, quick summaries and PDFs with 4 or more parts.
The app always shows which model it will use (and why) before you start.

**If a model's limit is reached**, NoteTube automatically switches to the other one and tells you
(*"3.8 Flash couldn't be used (its daily free limit is reached), so NoteTube switched to 3.5 Flash Lite"*).
It remembers this for the rest of your visit, so it doesn't waste time asking the used-up model again.
If both are used up, it tries a few other free backup models.

> Each note (or PDF part) = 1 request. NoteTube also saves quota by:
> - sending videos at **low resolution** (~1/3 the tokens, so videos up to roughly 40 minutes fit the 250K tokens/minute limit),
> - showing notes again from **History** (or when you repeat the same video + style + language) without a new request.
>
> Check your own limits at https://aistudio.google.com/rate-limit. To change models or limits, edit
> `BEST_MODEL`, `MORE_MODEL`, `FREE_LIMITS` and `FALLBACK_MODELS` at the top of `app.py`
> (current names: https://ai.google.dev/gemini-api/docs/models).

## 2. Run it on your Windows computer

Open **PowerShell** in the project folder (in File Explorer: click the address bar, type `powershell`, press Enter).

```powershell
# One-time setup
python -m venv venv                       # create a private Python space for this app
.\venv\Scripts\Activate.ps1               # switch into it (you'll see (venv) at the start of the line)
pip install -r requirements.txt           # install the packages

# Every time you want to run the app
.\venv\Scripts\Activate.ps1
streamlit run app.py
```

Your browser opens at **http://localhost:8501**.

- If PowerShell says *"running scripts is disabled"*, run this once, then try again:
  `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`
- **Your key:** either paste it in the app's sidebar each time, or put it once in
  `.streamlit\secrets.toml`:
  ```toml
  GEMINI_API_KEY = "AIza...your key..."
  ```
- **Test on your phone (same Wi-Fi):** open the *Network URL* that Streamlit prints (like `http://192.168.1.10:8501`).
- Stop the app with **Ctrl + C**.

## 3. Push to GitHub

1. Create a free account at **https://github.com** → **New repository** → name it `notetube` → **Create** (leave "Add README" unticked).
2. In PowerShell, in the project folder:
   ```powershell
   git init
   git add .
   git status                     # CHECK: secrets.toml and venv must NOT be listed
   git commit -m "NoteTube first version"
   git branch -M main
   git remote add origin https://github.com/YOUR-USERNAME/notetube.git
   git push -u origin main
   ```
   If `secrets.toml` appears in `git status`, stop and check that `.gitignore` is in the folder.

## 4. Deploy free on Streamlit Community Cloud

1. Go to **https://share.streamlit.io** → sign in with GitHub.
2. Click **Create app** → **Deploy a public app from GitHub**.
3. Repository: `YOUR-USERNAME/notetube`, Branch: `main`, Main file: `app.py`.
4. (Optional) Pick a nice URL, e.g. `notetube-cpv.streamlit.app`.
5. Open **Advanced settings → Secrets** and add your key (see step 5), then **Deploy**.

## 5. Add your key in Streamlit Cloud Secrets

In the app's **⋮ → Settings → Secrets** box, paste:

```toml
GEMINI_API_KEY = "AIza...your key..."
```

Save. The app restarts and uses the key automatically.

> ⚠️ If your app is public, **anyone with the link uses your free quota**. If that worries you,
> don't add the secret - then every visitor must paste their own key in the sidebar.
> You can also make the app private in Settings → Sharing.

**Heads-up:** YouTube often blocks caption downloads from cloud servers. On Streamlit Cloud,
method 1 (Gemini watches the video) usually still works; if not, use the paste-transcript box.

## 6. Add it to your phone's home screen

**Android (Chrome):** open your app URL → **⋮** menu → **Add to Home screen** → **Add**.
**iPhone (Safari):** open your app URL → **Share** button (□↑) → **Add to Home Screen** → **Add**.

It now opens like an app. 🎉
(Streamlit Cloud apps "sleep" after a few days without visits - just tap **Wake up** if you see that screen.)

---

## Troubleshooting

| Message | What to do |
|---|---|
| "Please paste your free Gemini API key" | Add the key in the sidebar or `secrets.toml` |
| "API key was rejected" | Copy the key again from aistudio.google.com/apikey |
| "free daily limit may be reached" | All free models are used up - try again tomorrow |
| "no captions" / "blocked" | Paste the transcript manually |
| "video is too long" | Use a shorter video or paste part of the transcript |
| Model not found | Update `BEST_MODEL` / `MORE_MODEL` at the top of `app.py` |
| "PDF is password-protected" | Remove the password (e.g. print to PDF) and upload again |
| "Stopped after part X" | Free limit reached - run the page range it shows later |
| "...switched to 3.5 Flash Lite" (or 3.8 Flash) | The chosen model was busy or out of quota; notes are still fine |
| "length limit" warning in the notes | Use a smaller page range for that chapter |
| Upload says "File must be 200MB or smaller" | Split the PDF first, or compress it with a tool you trust |
