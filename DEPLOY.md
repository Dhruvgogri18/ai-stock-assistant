# Deploy (free) + push to GitHub

## 1. Push to GitHub
```bash
git init && git add . && git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<you>/ai-stock-assistant.git
git push -u origin main
```
`.env` and `*.db` are git-ignored (secrets + 169 MB DB exceeds GitHub's 100 MB limit).

## 2. Upload the database once
```bash
gzip -9 -k stock.db        # ~71 MB
```
GitHub repo -> Releases -> Draft new release -> attach `stock.db.gz` -> publish. Copy the asset URL.
The container downloads it on first start via `DB_DOWNLOAD_URL`.

## 3. Host it free: Hugging Face Space (Docker, 16 GB RAM)
1. huggingface.co -> New Space -> SDK **Docker** -> Blank.
2. `git remote add space https://huggingface.co/spaces/<you>/<space>` and `git push space main`
   (or use "Sync from GitHub").
3. Space -> Settings -> Variables and secrets:
   - Variable `DB_DOWNLOAD_URL` = the release asset URL
   - Secret `FALLBACK_LLM_API_KEY` = free Gemini key (https://aistudio.google.com/apikey)
   - Secret `FALLBACK2_LLM_API_KEY` = free Groq key (https://console.groq.com/keys)
   - Do NOT set `LLM_API_KEY` (your company gateway) on a public app.
   - Optional: `DATA_PROVIDER`, `INDIANAPI_KEY`, Angel One keys.
4. Add `README.md` header for HF: `sdk: docker` and `app_port: 7860` (see below).

Any other Docker host (Render, Railway, Fly) also works: it reads `$PORT`.
Note: free hosts have ephemeral disk, so chat history resets on restart.

HF README front-matter (top of README.md, HF only):
```
---
title: Stock Research
sdk: docker
app_port: 7860
---
```
