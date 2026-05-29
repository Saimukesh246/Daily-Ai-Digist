<div align="center">

<img src="https://img.shields.io/badge/Python-3.11-3776AB?style=for-the-badge&logo=python&logoColor=white" />
<img src="https://img.shields.io/badge/FastAPI-0.111-009688?style=for-the-badge&logo=fastapi&logoColor=white" />
<img src="https://img.shields.io/badge/SQLite-embedded-003B57?style=for-the-badge&logo=sqlite&logoColor=white" />
<img src="https://img.shields.io/badge/Gemini_AI-1.5_Flash-4285F4?style=for-the-badge&logo=google&logoColor=white" />
<img src="https://img.shields.io/badge/Deploy-Railway-0B0D0E?style=for-the-badge&logo=railway&logoColor=white" />
<img src="https://img.shields.io/badge/License-MIT-22c55e?style=for-the-badge" />

# Daily AI Digest

**A self-hosted, fully autonomous AI intelligence dashboard.**

Crawls 7 live sources every day, synthesises everything through Google Gemini into a structured newsletter, and serves it as a glassmorphic dark-mode web app — with email delivery, full-text search, and zero paid services required.

[Features](#-features) · [Quick Start](#-quick-start) · [Deploy to Railway](#-deploy-to-railway) · [Configuration](#-configuration) · [API Reference](#-api-reference)

</div>

---

## ✨ Features

|   | Feature | Description |
|---|---|---|
| 📰 | **Autonomous daily crawl** | Pulls from Hacker News, Reddit, Hugging Face Papers, Arxiv, GitHub Trending, Product Hunt, and AI Lab Blogs |
| 🧠 | **Gemini AI synthesis** | `gemini-1.5-flash` condenses raw articles into 8 structured newsletter sections |
| 🔄 | **Offline fallback** | Keyword-driven engine runs when no API key is set — always produces a real, unique digest |
| 📧 | **Email delivery** | SMTP-based dispatch with subscriber management and automatic 7 AM daily send |
| ⚡ | **Spotlight search** | `Ctrl+K` full-text search across every crawled article with live source filter pills |
| 🖼️ | **OG image thumbnails** | Server-side Open Graph proxy with shimmer loading animations |
| 🔧 | **Scraper controls** | Toggle sources, set result limits, customise Reddit subreddits and GitHub keywords — all from the UI, no code edits |
| 📱 | **Mobile responsive** | Sliding drawer sidebar, stacked grid layout, bottom-sheet search |
| 🚄 | **Railway ready** | Dockerfile + `railway.toml` included; SQLite persisted via mounted Volume |

---

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- pip

### Run locally

```bash
# 1. Clone the repository
git clone https://github.com/Saimukesh246/Daily-Ai-Digist.git
cd Daily-Ai-Digist

# 2. Install dependencies
pip install -r requirements.txt

# 3. (Optional) set your Gemini API key
#    You can also paste it in the Settings modal after launch
export GEMINI_API_KEY=AIzaSy...        # macOS / Linux
# set GEMINI_API_KEY=AIzaSy...         # Windows CMD

# 4. Start the server
python app.py
```

Open **http://localhost:8000** and click **Sync Latest News**.

> **No Gemini key?** The app still works. The offline fallback uses keyword analysis on the actual crawled articles to produce a real, unique digest every day.

---

## 📦 Project Structure

```
Daily-Ai-Digist/
├── app.py            # FastAPI server — all endpoints, OG proxy, background scheduler
├── fetcher.py        # Per-source crawlers (HN, Reddit, HuggingFace, Arxiv, GitHub…)
├── analyzer.py       # Gemini synthesis + intelligent offline fallback engine
├── emailer.py        # SMTP HTML/plain-text email builder and sender
├── database.py       # SQLite helpers: articles, digests, settings, subscribers
├── requirements.txt
├── Dockerfile
├── railway.toml
└── static/
    ├── index.html      # Single-page dashboard (no framework)
    ├── css/styles.css  # Glassmorphic dark-mode design system
    └── js/app.js       # Vanilla JS — API client, card renderers, search, settings
```

---

## 💻 Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Python 3.11, FastAPI, Uvicorn |
| **Database** | SQLite via stdlib `sqlite3` |
| **AI** | Google Gemini API (`google-generativeai`) |
| **Scraping** | `requests`, `BeautifulSoup4`, `xml.etree.ElementTree` |
| **Email** | stdlib `smtplib` / `email.mime` — zero extra dependencies |
| **Frontend** | Vanilla JS + CSS — no framework, no bundler, no build step |

---

## 🚄 Deploy to Railway

Railway is the recommended host because it supports always-on servers, background threads, and persistent volumes — all of which this app requires.

### Steps

1. **Fork or push** this repo to your GitHub account.
2. Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo** → select this repo.
3. Railway auto-detects the `Dockerfile` via `railway.toml`. No extra config needed.
4. Add a **Volume** and mount it at `/app/data`.
   This is where `ai_digest.db` lives — it survives every redeploy.
5. Add environment variables in Railway → **Variables**:

| Variable | Description |
|---|---|
| `GEMINI_API_KEY` | Your [Google AI Studio](https://aistudio.google.com) API key |
| `APP_URL` | Your Railway public URL, e.g. `https://your-app.up.railway.app` |

The health-check endpoint `GET /api/status` is pre-configured in `railway.toml` and keeps Railway's restart policy informed.

---

## ⚙️ Configuration

All settings are stored in SQLite and editable from the **Settings** modal in the dashboard. No config files, no restarts needed.

### Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `GEMINI_API_KEY` | No | — | Gemini key for AI synthesis. Can also be saved in-app via Settings. |
| `DATA_DIR` | No | App directory | Directory for `ai_digest.db`. Set to `/app/data` in Docker. |
| `PORT` | No | `8000` | HTTP port. Set automatically by Railway. |
| `APP_URL` | No | `http://localhost:8000` | Public base URL used in email CTA links. |
| `RAILWAY_ENVIRONMENT` | Auto | — | When present, binds `0.0.0.0` instead of `127.0.0.1`. |

### Email Delivery

1. Open **Settings → Email Delivery** in the dashboard.
2. Enter your SMTP credentials.
3. Add subscribers and enable the **Auto-send** toggle.
4. The digest is dispatched automatically at **7 AM** daily.
5. Use **Send Test** to verify delivery before going live.

**Gmail setup:** Google Account → Security → 2-Step Verification → App Passwords → generate one for *Mail*.
Use `smtp.gmail.com` · port `587` · your Gmail address as username · the 16-character app password.

### Scraper Controls

Open **Settings → Sources** to configure each data source without touching code:

- **Toggle on/off** — disabled sources are skipped on the next sync
- **Max results** — control how many articles each source fetches (1–50)
- **Reddit subreddits** — add or remove via tag-chip input (press Enter or comma to add)
- **GitHub keywords** — customise the search terms used to find trending repositories

Changes are saved to SQLite and take effect on the next **Sync Latest News**.

---

## 📡 API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Dashboard SPA |
| `GET` | `/api/status` | Crawler status + live console logs |
| `POST` | `/api/trigger` | Trigger a fetch-and-analyse run |
| `GET` | `/api/digests` | List all digest dates |
| `GET` | `/api/digests/latest` | Latest compiled digest |
| `GET` | `/api/digests/{date}` | Digest for a specific date (`YYYY-MM-DD`) |
| `GET` | `/api/search?q=` | Full-text search across all crawled articles |
| `GET` | `/api/og-image?url=` | Server-side Open Graph image proxy |
| `GET / POST` | `/api/settings` | Gemini API key |
| `GET / POST` | `/api/settings/email` | SMTP configuration |
| `GET / POST` | `/api/settings/scraper` | Per-source scraper configuration |
| `GET` | `/api/subscribers` | List all subscribers |
| `POST` | `/api/subscribers` | Add a subscriber |
| `DELETE` | `/api/subscribers/{email}` | Remove a subscriber |
| `POST` | `/api/email/test` | Send a one-off test digest email |

---

## 📄 License

This project is licensed under the **MIT License** — free to use, modify, and deploy.

---

<div align="center">

Built with Python, FastAPI, and too much curiosity about AI news.

⭐ **Star the repo if you find it useful!**

</div>
