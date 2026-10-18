# 📝 NoteTube

Paste a YouTube link → get clean notes in **English, Hinglish or Hindi**.
Copy them, or download as **.md, .txt or PDF**. Works on phone, tablet and laptop. 100% free.

**Note styles:** Quick summary · Detailed study notes · Action items and takeaways · Content ideas for CPV FitLab (5 Instagram carousels + 3 Reels hooks)

---

## How it reads a video

1. **Gemini watches the video directly** from its YouTube URL (public videos only).
2. If that fails → it downloads the video's **captions** and sends the text to Gemini.
3. If that fails too → it tells you why, and you can **paste a transcript** yourself
   (on YouTube: ⋯ More → *Show transcript* → copy the text).

## Files

| File | What it is |
|---|---|
| `app.py` | The whole app (UI, prompts, Gemini + YouTube logic) |
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

> **Free limits** (example for Gemini 3.8 Flash): 5 requests/minute, 250K tokens/minute, 20 requests/day.
> Each note = 1 request. NoteTube saves your quota by:
> - sending videos at **low resolution** (~1/3 the tokens, so videos up to roughly 40 minutes fit the 250K/minute limit),
> - switching to **other free models** (each has its own separate limits) when one runs out,
> - showing notes again from **History** (or when you repeat the same video + style + language) without a new request.
>
> If you see "free daily limit may be reached", try again tomorrow.
> To change the AI model, edit `MODEL_NAME` / `FALLBACK_MODELS` at the top of `app.py`
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
| "free daily limit may be reached" | Wait until tomorrow, or try a shorter video |
| "no captions" / "blocked" | Paste the transcript manually |
| "video is too long" | Use a shorter video or paste part of the transcript |
| Model not found | Update `MODEL_NAME` at the top of `app.py` |
