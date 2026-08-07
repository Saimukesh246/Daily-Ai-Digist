import os
import time
import logging
import threading
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

# Configured first, before any other module-level `logging.basicConfig` call
# elsewhere in the codebase can install a conflicting plain-text handler.
from logging_config import configure_logging
configure_logging()
logger = logging.getLogger("app")

from fastapi import FastAPI, BackgroundTasks, HTTPException, Response, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional

import requests as _requests
from bs4 import BeautifulSoup as _BeautifulSoup
from urllib.parse import urlparse as _urlparse, urljoin as _urljoin_for_redirect
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Load .env file if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Auth libraries
try:
    import bcrypt as _bcrypt
    BCRYPT_AVAILABLE = True
except ImportError:
    BCRYPT_AVAILABLE = False
    _bcrypt = None
    logger.warning(
        "bcrypt is not installed — falling back to unsalted SHA-256 for password "
        "hashing. This is significantly weaker; install bcrypt as soon as possible."
    )

import database
import fetcher
import analyzer
import emailer
import weekly_emailer

# ---------------------------------------------------------------------------
# Lifespan (replaces deprecated @app.on_event)
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app_instance: "FastAPI"):
    start_hourly_scheduler()
    yield

# Initialize FastAPI application
app = FastAPI(title="Daily AI Digest Server", version="1.0.0", lifespan=lifespan)

# Rate limiting — guards login/register against brute force & credential stuffing
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Setup folder directories
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(os.path.join(STATIC_DIR, "css"), exist_ok=True)
os.makedirs(os.path.join(STATIC_DIR, "js"), exist_ok=True)

# Mount the static files directory
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Database initialization
DB_PATH = database.DEFAULT_DB_PATH
database.init_db(DB_PATH)

# In-memory OG image cache
_og_cache: dict = {}

# Global synchronization progress state
SYNC_STATUS = {
    "status": "idle",
    "current_step": "System ready",
    "logs": ["Server initialized."],
    "error_message": "",
    "completed_at": None
}

# Shared secret for the external cron trigger (/api/cron/hourly) — lets a free
# external pinger (cron-job.org etc.) drive the hourly job on hosts like Render's
# free tier, where the process sleeps and the in-process scheduler thread dies with it.
CRON_SECRET = os.environ.get("CRON_SECRET", "")

# ---------------------------------------------------------------------------
# Security / Auth helpers
# ---------------------------------------------------------------------------

security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    if BCRYPT_AVAILABLE and _bcrypt:
        hashed = _bcrypt.hashpw(password.encode("utf-8"), _bcrypt.gensalt())
        return hashed.decode("utf-8")
    import hashlib
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(plain: str, hashed: str) -> bool:
    if BCRYPT_AVAILABLE and _bcrypt:
        try:
            return _bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
        except Exception:
            return False
    import hashlib
    return hashlib.sha256(plain.encode()).hexdigest() == hashed


def generate_token() -> str:
    return secrets.token_urlsafe(48)


def assert_public_http_url(url: str):
    """Raises ValueError if the URL isn't http(s) or resolves to a private/internal/
    loopback/link-local address. Prevents SSRF when the server fetches a user- or
    admin-supplied URL on the caller's behalf (og-image proxy, source validation)."""
    import socket
    import ipaddress
    parsed = _urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http/https URLs are allowed.")
    if not parsed.hostname:
        raise ValueError("URL has no hostname.")
    try:
        addrinfos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        raise ValueError("Could not resolve hostname.")
    for *_rest, sockaddr in addrinfos:
        ip = ipaddress.ip_address(sockaddr[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("URL resolves to a non-public address.")


def get_smtp_settings() -> dict:
    return {
        "host":      database.get_setting(DB_PATH, "smtp_host", ""),
        "port":      int(database.get_setting(DB_PATH, "smtp_port", "587")),
        "user":      database.get_setting(DB_PATH, "smtp_user", ""),
        "password":  database.get_setting(DB_PATH, "smtp_password", ""),
        "from_name": database.get_setting(DB_PATH, "smtp_from_name", "Daily AI Digest"),
    }


def get_current_user_optional(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    """Returns the current user dict or None if unauthenticated."""
    if not credentials:
        return None
    token   = credentials.credentials
    session = database.get_session(DB_PATH, token)
    if not session:
        return None
    user = database.get_user_by_id(DB_PATH, session["user_id"])
    return user


def require_auth(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    """Dependency: raises 401 if the request has no valid auth token."""
    user = get_current_user_optional(credentials)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required. Please log in.")
    return user


def require_admin(user: dict = Depends(require_auth)):
    """Dependency: raises 403 if the authenticated user is not an admin."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required. Contact your administrator.")
    return user

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class SettingsPayload(BaseModel):
    gemini_api_key: str

class EmailSettingsPayload(BaseModel):
    smtp_host: str
    smtp_port: int = 587
    smtp_user: str
    smtp_password: str = ""
    from_name: str = "Daily AI Digest"
    enabled: bool = True

class SubscriberPayload(BaseModel):
    email: str
    name: str = ""

class TestEmailPayload(BaseModel):
    to: str
    date: str = ""

class ScraperSettingsPayload(BaseModel):
    config: dict

class SourcePayload(BaseModel):
    name: str
    url: str
    source_type: str = "rss"
    enabled: bool = True

# Auth payloads
class RegisterPayload(BaseModel):
    email: str
    password: str
    name: str = ""

class LoginPayload(BaseModel):
    email: str
    password: str

# ---------------------------------------------------------------------------
# Logging helper
# ---------------------------------------------------------------------------

def add_log(message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    log_line  = f"[{timestamp}] {message}"
    logger.info(message)
    SYNC_STATUS["logs"].append(log_line)
    SYNC_STATUS["current_step"] = message

# ---------------------------------------------------------------------------
# Background sync job
# ---------------------------------------------------------------------------

def run_sync_job(date_str):
    global SYNC_STATUS
    try:
        SYNC_STATUS["status"]        = "fetching"
        SYNC_STATUS["error_message"] = ""
        SYNC_STATUS["completed_at"]  = None
        SYNC_STATUS["logs"]          = []

        add_log("Starting Daily AI Digest gathering job...")

        # Clear today's cached articles so every sync fetches completely fresh content
        cleared = database.clear_articles_for_date(DB_PATH, date_str)
        if cleared:
            add_log(f"Cleared {cleared} cached articles — fetching fresh content from all sources...")
        else:
            add_log("No cached articles for today — fetching fresh content from all sources...")

        cfg = database.get_scraper_config(DB_PATH)

        # All sources are independent network calls — fetch them concurrently
        # instead of one after another. This was the largest contributor to
        # slow syncs (lab_blogs alone hits ~25+ feeds; doing all 7 sources
        # sequentially could take minutes).
        hn_cfg = cfg.get("hacker_news", {})
        rd_cfg = cfg.get("reddit", {})
        hf_cfg = cfg.get("huggingface", {})
        ax_cfg = cfg.get("arxiv", {})
        gh_cfg = cfg.get("github", {})
        ph_cfg = cfg.get("product_hunt", {})
        lb_cfg = cfg.get("lab_blogs", {})

        jobs = {}
        if hn_cfg.get("enabled", True):
            jobs["Hacker News"] = lambda: fetcher.fetch_hacker_news_ai(date_str, limit=hn_cfg.get("limit", 20))
        else:
            add_log("-> Hacker News: Skipped (disabled).")

        if rd_cfg.get("enabled", True):
            subs = rd_cfg.get("subreddits", ["MachineLearning", "singularity", "ArtificialIntelligence"])
            jobs["Reddit"] = lambda: fetcher.fetch_reddit_ai(subreddits=subs, limit=rd_cfg.get("limit", 10))
        else:
            add_log("-> Reddit: Skipped (disabled).")

        if hf_cfg.get("enabled", True):
            jobs["Hugging Face"] = lambda: fetcher.fetch_huggingface_papers(limit=hf_cfg.get("limit", 15))
        else:
            add_log("-> Hugging Face: Skipped (disabled).")

        if ax_cfg.get("enabled", True):
            jobs["Arxiv"] = lambda: fetcher.fetch_arxiv_ai(limit=ax_cfg.get("limit", 15))
        else:
            add_log("-> Arxiv: Skipped (disabled).")

        if gh_cfg.get("enabled", True):
            jobs["GitHub Trending"] = lambda: fetcher.fetch_github_trending(
                keywords=gh_cfg.get("keywords"), limit=gh_cfg.get("limit", 15)
            )
        else:
            add_log("-> GitHub Trending: Skipped (disabled).")

        if ph_cfg.get("enabled", True):
            jobs["Product Hunt"] = lambda: fetcher.fetch_product_hunt_ai()
        else:
            add_log("-> Product Hunt: Skipped (disabled).")

        if lb_cfg.get("enabled", True):
            jobs["Lab Blogs"] = lambda: fetcher.fetch_lab_blogs()
        else:
            add_log("-> Lab Blogs: Skipped (disabled).")

        add_log(f"Crawling {len(jobs)} sources in parallel...")
        all_items = []
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=max(len(jobs), 1)) as executor:
            future_to_name = {executor.submit(fn): name for name, fn in jobs.items()}
            for future in as_completed(future_to_name):
                name = future_to_name[future]
                try:
                    items = future.result()
                except Exception as e:
                    logger.error(f"Error fetching {name}: {e}")
                    items = []
                add_log(f"-> {name}: Found {len(items)} items.")
                all_items.extend(items)

        unique_items = []
        seen_urls    = set()
        for item in all_items:
            url = item.get("url")
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique_items.append(item)

        add_log(f"Deduplication completed. Total unique entries: {len(unique_items)}")
        add_log("Saving raw articles to database...")

        saved_count = database.save_raw_articles_bulk(DB_PATH, date_str, unique_items)
        add_log(f"-> Saved {saved_count} new entries.")

        SYNC_STATUS["status"] = "analyzing"
        add_log("Entering Synthesis stage...")

        api_key = database.get_setting(DB_PATH, "gemini_api_key") or os.environ.get("GEMINI_API_KEY")
        if api_key:
            add_log("Gemini API key detected. Initiating synthesis model...")
        else:
            add_log("No Gemini API key — activating offline fallback engine...")

        # If today's digest was already compiled earlier (e.g. someone clicked Sync
        # twice in a row), capture what it showed before wiping it — so the new
        # synthesis can be told to avoid repeating itself when re-syncing minutes
        # later against largely the same underlying news.
        previous_digest = database.get_digest(DB_PATH, date_str)
        previously_shown_titles = []
        if previous_digest and previous_digest.get("content"):
            prev_content = previous_digest["content"]
            previously_shown_titles = [n.get("headline", "") for n in prev_content.get("biggest_news", [])]
            previously_shown_titles += [t.get("tool", "") for t in prev_content.get("discovered_tools", [])]
            previously_shown_titles = [t for t in previously_shown_titles if t]

        # Always force-regenerate — delete stale digest so analyze starts fresh
        database.save_digest(DB_PATH, date_str, {})  # placeholder wipe
        digest, mode = analyzer.generate_digest(DB_PATH, date_str, api_key, previously_shown_titles)

        if "fallback" in mode:
            add_log("Intelligent offline synthesis complete.")
        else:
            add_log("Gemini synthesis completed successfully!")

        SYNC_STATUS["status"]       = "complete"
        SYNC_STATUS["completed_at"] = datetime.now().isoformat()
        add_log(f"Successfully compiled Daily AI Digest for {date_str}!")

    except Exception as e:
        logger.exception("Synchronization job failed:")
        SYNC_STATUS["status"]        = "error"
        SYNC_STATUS["error_message"] = str(e)
        add_log(f"CRITICAL ERROR: {e}")

# ---------------------------------------------------------------------------
# WEB ENDPOINTS
# ---------------------------------------------------------------------------

@app.get("/healthz")
async def healthz():
    """Liveness/readiness probe — verifies the app can actually reach Postgres,
    not just that the process is running. No auth required (used by uptime
    monitors / Render's health checks)."""
    try:
        conn = database.get_db_connection()
        try:
            conn.cursor().execute("SELECT 1")
        finally:
            database.release_db_connection(conn)
        return {"status": "ok", "database": "ok"}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JSONResponse(status_code=503, content={"status": "error", "database": "unreachable", "detail": str(e)})


def _esc_attr(value: str) -> str:
    return (value or "").replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


@app.get("/", response_class=HTMLResponse)
async def serve_blog(request: Request):
    """Public, unauthenticated News Blog homepage — the digest's public face."""
    blog_path = os.path.join(STATIC_DIR, "blog.html")
    if not os.path.exists(blog_path):
        return """
        <html>
            <head><title>Daily AI Digest</title><style>body {background:#0f172a; color:#f8fafc; font-family:sans-serif; text-align:center; padding:100px;}</style></head>
            <body><h1>Daily AI Digest Engine Online</h1><p>The static frontend is being generated. Please refresh in a moment...</p></body>
        </html>
        """
    with open(blog_path, "r", encoding="utf-8") as f:
        html = f.read()

    # Fill in Open Graph / Twitter Card tags from today's lead story so shared
    # links render a real preview card instead of generic boilerplate.
    og_title = "AI Digest — Daily Intelligence Briefing"
    og_description = "A daily briefing on AI news, research, tools, and market movement — synthesized from 7+ live sources."
    og_image = ""
    try:
        digest = database.get_latest_digest(DB_PATH)
        biggest_news = (digest or {}).get("content", {}).get("biggest_news") or []
        if biggest_news:
            lead = biggest_news[0]
            og_title = f"{lead['headline']} — AI Digest"
            og_description = (lead.get("summary") or og_description)[:200]
            og_image = _fetch_og_image(lead.get("link", "")).get("image_url", "")
    except Exception:
        logger.exception("Failed to build OG tags for blog homepage:")

    og_url = os.environ.get("APP_URL") or str(request.base_url).rstrip("/")

    html = (html
        .replace("{{OG_TITLE}}", _esc_attr(og_title))
        .replace("{{OG_DESCRIPTION}}", _esc_attr(og_description))
        .replace("{{OG_URL}}", _esc_attr(og_url))
        .replace("{{OG_IMAGE}}", _esc_attr(og_image)))

    return HTMLResponse(content=html)


@app.get("/dashboard", response_class=HTMLResponse)
@app.get("/dashboard/", response_class=HTMLResponse)
async def serve_dashboard():
    """Authenticated dashboard SPA — sync controls, settings, sources, subscribers."""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(index_path):
        return """
        <html>
            <head><title>Daily AI Digest</title><style>body {background:#0f172a; color:#f8fafc; font-family:sans-serif; text-align:center; padding:100px;}</style></head>
            <body><h1>Daily AI Digest Engine Online</h1><p>The static frontend is being generated. Please refresh in a moment...</p></body>
        </html>
        """
    with open(index_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/article.html", response_class=HTMLResponse)
async def serve_article():
    """Public article reading view — content is fetched client-side from
    /api/public/article using the ?date=&section=&index= query params."""
    article_path = os.path.join(STATIC_DIR, "article.html")
    with open(article_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/rss.xml")
async def serve_rss():
    """Public RSS 2.0 feed of the latest digest — Top Stories, Research, Tools, Market."""
    from xml.sax.saxutils import escape as _xml_escape
    from email.utils import format_datetime as _rfc822

    digest = database.get_latest_digest(DB_PATH)
    site_url = os.environ.get("APP_URL", "")
    content = (digest or {}).get("content", {})

    try:
        pub_date = datetime.strptime(digest["date"], "%Y-%m-%d") if digest else datetime.utcnow()
    except Exception:
        pub_date = datetime.utcnow()

    entries = []
    for item in (content.get("biggest_news") or []):
        entries.append(("Top Stories", item.get("headline", ""), item.get("summary", ""), item.get("link", "")))
    for item in (content.get("open_source_research") or []):
        entries.append((item.get("category", "Research"), item.get("title", ""), item.get("summary", ""), item.get("link", "")))
    for item in (content.get("discovered_tools") or []):
        entries.append(("New AI Tools", item.get("tool", ""), item.get("what_it_does", ""), item.get("link", "")))
    for item in (content.get("market_industry") or []):
        entries.append((item.get("category", "Market"), item.get("headline", ""), item.get("summary", ""), item.get("link", "")))

    items_xml = ""
    for category, title, description, link in entries:
        if not title or not link:
            continue
        items_xml += f"""
    <item>
      <title>{_xml_escape(title)}</title>
      <link>{_xml_escape(link)}</link>
      <guid isPermaLink="false">{_xml_escape(link)}</guid>
      <category>{_xml_escape(category)}</category>
      <description>{_xml_escape(description)}</description>
      <pubDate>{_rfc822(pub_date)}</pubDate>
    </item>"""

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>AI Digest — Daily Intelligence Briefing</title>
  <link>{_xml_escape(site_url)}</link>
  <description>A daily briefing on AI news, research, tools, and market movement — synthesized from 7+ live sources.</description>
  <language>en-us</language>
  <lastBuildDate>{_rfc822(pub_date)}</lastBuildDate>{items_xml}
</channel>
</rss>"""

    return Response(content=xml, media_type="application/rss+xml")

# ---------------------------------------------------------------------------
# AUTH ENDPOINTS
# ---------------------------------------------------------------------------

@app.post("/api/auth/register")
@limiter.limit("5/minute")
async def register(payload: RegisterPayload, request: Request):
    """Register a new user with email + password."""
    import re
    email = payload.email.strip().lower()
    if not re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(status_code=400, detail="Invalid email address.")
    if len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")

    existing = database.get_user_by_email(DB_PATH, email)
    if existing:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    name          = payload.name.strip() or email.split("@")[0]
    password_hash = hash_password(payload.password)
    # First registered user becomes admin automatically
    role = "admin" if database.get_user_count(DB_PATH) == 0 else "viewer"
    user = database.create_user(DB_PATH, email, name=name, password_hash=password_hash, role=role)

    if not user:
        raise HTTPException(status_code=500, detail="Failed to create account.")

    token = generate_token()
    database.save_session(DB_PATH, token, user["id"])
    database.update_user_last_login(DB_PATH, user["id"])

    smtp = get_smtp_settings()
    threading.Thread(
        target=emailer.send_login_confirmation_email,
        args=(smtp, email, name, datetime.utcnow(), "Email Registration"),
        daemon=True
    ).start()

    return {
        "token": token,
        "user":  {"id": user["id"], "email": user["email"], "name": user["name"],
                  "avatar_url": user.get("avatar_url", ""), "role": user.get("role", "viewer")},
        "message": "Account created successfully."
    }


@app.post("/api/auth/login")
@limiter.limit("10/minute")
async def login(payload: LoginPayload, request: Request):
    """Login with email + password."""
    email = payload.email.strip().lower()
    user  = database.get_user_by_email(DB_PATH, email)

    if not user or not user.get("password_hash"):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    if not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    token = generate_token()
    database.save_session(DB_PATH, token, user["id"])
    database.update_user_last_login(DB_PATH, user["id"])

    # Send login confirmation email in background
    smtp = get_smtp_settings()
    threading.Thread(
        target=emailer.send_login_confirmation_email,
        args=(smtp, email, user.get("name", ""), datetime.utcnow(), "Email & Password"),
        daemon=True
    ).start()

    return {
        "token": token,
        "user":  {"id": user["id"], "email": user["email"], "name": user["name"],
                  "avatar_url": user.get("avatar_url", ""), "role": user.get("role", "viewer")},
        "message": "Login successful."
    }


@app.get("/api/auth/me")
async def get_me(current_user: dict = Depends(require_auth)):
    """Returns info about the currently authenticated user."""
    return {
        "id":        current_user["id"],
        "email":     current_user["email"],
        "name":      current_user["name"],
        "avatar_url": current_user.get("avatar_url", ""),
        "created_at": current_user.get("created_at", ""),
        "last_login": current_user.get("last_login", ""),
        "role":       current_user.get("role", "viewer"),
    }


@app.post("/api/auth/logout")
async def logout(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    """Invalidates the current session token."""
    if credentials:
        database.delete_session(DB_PATH, credentials.credentials)
    return {"message": "Logged out successfully."}


# ---------------------------------------------------------------------------
# DATA API ENDPOINTS (protected by auth)
# ---------------------------------------------------------------------------

@app.get("/api/search")
async def search_articles(q: str = "", source: str = "", limit: int = 40,
                           _user: dict = Depends(require_auth)):
    results = database.search_articles(DB_PATH, q.strip(), source.strip() or None, limit)
    sources = database.get_distinct_sources(DB_PATH)
    return {"results": results, "sources": sources, "total": len(results)}


def _fetch_og_image(url: str) -> dict:
    if url in _og_cache:
        return _og_cache[url]
    result = {"image_url": "", "title": ""}
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        next_url = url
        for _ in range(5):
            assert_public_http_url(next_url)
            resp = _requests.get(next_url, headers=headers, timeout=6, allow_redirects=False)
            if resp.is_redirect and resp.headers.get("Location"):
                next_url = _urljoin_for_redirect(next_url, resp.headers["Location"])
                continue
            break
        soup = _BeautifulSoup(resp.text, "html.parser")
        for attr, val in [("property", "og:image"), ("name", "twitter:image"), ("property", "twitter:image"), ("name", "og:image")]:
            tag = soup.find("meta", {attr: val})
            if tag and tag.get("content"):
                img_val = tag["content"].strip()
                if img_val.startswith("//"):
                    img_val = "https:" + img_val
                result["image_url"] = urllib.parse.urljoin(next_url, img_val)
                break
        if not result["image_url"]:
            link_tag = soup.find("link", {"rel": "image_src"})
            if link_tag and link_tag.get("href"):
                img_val = link_tag["href"].strip()
                if img_val.startswith("//"):
                    img_val = "https:" + img_val
                result["image_url"] = urllib.parse.urljoin(next_url, img_val)

        if not result["image_url"]:
            for img_tag in soup.find_all("img", src=True):
                src = img_tag.get("src", "").strip()
                if src and not src.startswith("data:") and any(ext in src.lower() for ext in [".png", ".jpg", ".jpeg", ".webp", ".svg"]):
                    if src.startswith("//"):
                        src = "https:" + src
                    result["image_url"] = urllib.parse.urljoin(next_url, src)
                    break

        for attr, val in [("property", "og:title"), ("name", "twitter:title"), ("property", "twitter:title")]:
            tag = soup.find("meta", {attr: val})
            if tag and tag.get("content"):
                result["title"] = tag["content"].strip()
                break
        if not result["title"] and soup.title:
            result["title"] = soup.title.string.strip()
    except Exception:
        pass
    _og_cache[url] = result
    return result


@app.get("/api/og-image")
async def get_og_image(url: str, _user: dict = Depends(require_auth)):
    return _fetch_og_image(url)


@app.get("/api/public/og-image-proxy")
@limiter.limit("60/minute")
async def public_og_image_proxy(request: Request, url: str):
    """Proxies and streams external image data to bypass CORS/hotlinking restrictions."""
    try:
        assert_public_http_url(url)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
        }
        resp = _requests.get(url, headers=headers, timeout=8, stream=True)
        if resp.status_code == 200:
            ct = resp.headers.get("Content-Type", "image/jpeg")
            if not ct or "text/html" in ct:
                ct = "image/jpeg"
            return Response(
                content=resp.content,
                media_type=ct,
                headers={"Cache-Control": "public, max-age=86400"}
            )
    except Exception:
        pass
    raise HTTPException(status_code=404, detail="Image proxy failed.")


@app.get("/api/digests")
async def list_digests(_user: dict = Depends(require_auth)):
    dates = database.get_all_digest_dates(DB_PATH)
    return {"dates": dates}


@app.get("/api/digests/latest")
async def get_latest_digest(_user: dict = Depends(require_auth)):
    digest = database.get_latest_digest(DB_PATH)
    if not digest:
        raise HTTPException(status_code=404, detail="No digests generated yet.")
    return digest


@app.get("/api/digests/{date}")
async def get_digest_by_date(date: str, _user: dict = Depends(require_auth)):
    digest = database.get_digest(DB_PATH, date)
    if not digest:
        raise HTTPException(status_code=404, detail=f"Digest for date {date} not found.")
    return digest


# ---------------------------------------------------------------------------
# PUBLIC ENDPOINTS — power the unauthenticated News Blog homepage (static/blog.html).
# No session required; each is rate-limited or read-only to bound abuse.
# ---------------------------------------------------------------------------

@app.get("/api/public/digest")
async def get_public_latest_digest():
    digest = database.get_latest_digest(DB_PATH)
    if not digest:
        raise HTTPException(status_code=404, detail="No digests generated yet.")
    return digest


# Sections with individually-linkable items (i.e. each entry has its own
# outbound `link`) — these are the ones the blog gives an internal reading
# view to. Sections like what_changed/quick_takes have no per-item link.
_ARTICLE_SECTIONS = {
    "biggest_news":         {"title_key": "headline", "label": "Top Stories"},
    "discovered_tools":     {"title_key": "tool",      "label": "New AI Tools Discovered"},
    "open_source_research": {"title_key": "title",     "label": "Open Source & Technical Research"},
    "market_industry":      {"title_key": "headline",  "label": "Market & Industry Movements"},
}


@app.get("/api/public/article")
async def get_public_article(date: str, section: str, index: int):
    if section not in _ARTICLE_SECTIONS:
        raise HTTPException(status_code=400, detail="Unknown section.")
    digest = database.get_digest(DB_PATH, date)
    if not digest:
        raise HTTPException(status_code=404, detail="Digest not found for that date.")
    items = digest.get("content", {}).get(section) or []
    if index < 0 or index >= len(items):
        raise HTTPException(status_code=404, detail="Article not found.")

    related = []
    for sec, meta in _ARTICLE_SECTIONS.items():
        if sec == section:
            continue
        sec_items = digest.get("content", {}).get(sec) or []
        if sec_items:
            related.append({
                "section": sec,
                "index": 0,
                "label": meta["label"],
                "title": sec_items[0].get(meta["title_key"], ""),
                "link": sec_items[0].get("link", ""),
            })

    return {
        "date": date,
        "section": section,
        "label": _ARTICLE_SECTIONS[section]["label"],
        "index": index,
        "item": items[index],
        "related": related,
    }


@app.get("/api/public/og-image")
@limiter.limit("30/minute")
async def get_public_og_image(request: Request, url: str):
    return _fetch_og_image(url)


@app.post("/api/public/subscribe")
@limiter.limit("5/minute")
async def public_subscribe(request: Request, payload: SubscriberPayload):
    import re
    if not re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+", payload.email):
        raise HTTPException(status_code=400, detail="Invalid email address.")
    added = database.add_subscriber(DB_PATH, payload.email, payload.name)
    if not added:
        raise HTTPException(status_code=409, detail=f"{payload.email} is already subscribed.")

    smtp = get_smtp_settings()
    threading.Thread(
        target=emailer.send_subscribe_confirmation_email,
        args=(smtp, payload.email, payload.name),
        daemon=True
    ).start()

    return {"message": f"{payload.email} added successfully."}


@app.get("/api/public/digest/timeline")
@limiter.limit("30/minute")
async def get_public_digest_timeline(request: Request, before_date: str):
    """Public endpoint for infinite scroll: fetches or auto-synthesizes past daily briefings for ALL calendar days continuously."""
    try:
        curr_d = datetime.strptime(before_date.strip(), "%Y-%m-%d")
    except Exception:
        curr_d = datetime.utcnow()

    for i in range(1, 31):
        target_date = (curr_d - timedelta(days=i)).strftime("%Y-%m-%d")

        digest = database.get_digest(DB_PATH, target_date)
        if digest and digest.get("content"):
            return {"has_more": True, "date": target_date, "digest": digest}

        raw_items = database.get_raw_articles_by_date(DB_PATH, target_date)
        if not raw_items:
            raw_items = database.get_raw_articles_since(DB_PATH, target_date, days=1)

        if raw_items:
            import analyzer
            synth_content = analyzer._offline_fallback_analysis(raw_items)
            digest_obj = {"id": 0, "date": target_date, "content": synth_content, "created_at": target_date}
            database.save_digest(DB_PATH, target_date, synth_content)
            return {"has_more": True, "date": target_date, "digest": digest_obj}
        else:
            recent_items = database.get_raw_articles_stream(DB_PATH, limit=12, offset=(i - 1) * 6)
            if recent_items:
                import analyzer
                synth_content = analyzer._offline_fallback_analysis(recent_items)
                digest_obj = {"id": 0, "date": target_date, "content": synth_content, "created_at": target_date}
                database.save_digest(DB_PATH, target_date, synth_content)
                return {"has_more": True, "date": target_date, "digest": digest_obj}

    return {"has_more": False, "date": "", "digest": None}


@app.get("/api/public/articles/stream")
@limiter.limit("40/minute")
async def get_public_articles_stream(request: Request, limit: int = 15, offset: int = 0, source: str = ""):
    """Public endpoint for the infinite raw article stream with source filters."""
    limit = min(max(1, limit), 50)
    offset = max(0, offset)
    articles = database.get_raw_articles_stream(DB_PATH, limit=limit, offset=offset, source_filter=source.strip())
    return {
        "articles": articles,
        "offset": offset,
        "limit": limit,
        "has_more": len(articles) == limit
    }


@app.post("/api/trigger")
async def trigger_sync(background_tasks: BackgroundTasks, date: str = None,
                        _user: dict = Depends(require_admin)):
    global SYNC_STATUS
    if SYNC_STATUS["status"] in ["fetching", "analyzing"]:
        return JSONResponse(status_code=400, content={"detail": "A synchronization run is already active."})
    target_date = date or datetime.now().strftime("%Y-%m-%d")
    background_tasks.add_task(run_sync_job, target_date)
    return {"message": "Aggregation task triggered.", "date": target_date}


@app.api_route("/api/cron/hourly", methods=["GET", "POST"])
async def trigger_hourly_cron(request: Request, background_tasks: BackgroundTasks):
    """Externally-triggered equivalent of the in-process hourly scheduler, for
    hosts (e.g. Render's free tier) that sleep the process when idle. Point an
    external cron pinger (cron-job.org, EasyCron, etc.) at this URL hourly with
    the shared secret, either as `X-Cron-Secret` header or `?secret=` query param
    (query param exists for pingers that can't set custom headers)."""
    if not CRON_SECRET:
        raise HTTPException(status_code=503, detail="CRON_SECRET is not configured on the server.")
    provided = request.headers.get("X-Cron-Secret") or request.query_params.get("secret")
    if provided != CRON_SECRET:
        raise HTTPException(status_code=401, detail="Invalid or missing cron secret.")
    background_tasks.add_task(run_scheduled_tasks)
    return {"message": "Hourly scheduled tasks triggered."}


@app.get("/api/status")
async def get_status(_user: dict = Depends(require_auth)):
    return SYNC_STATUS


@app.get("/api/settings")
async def get_settings(_user: dict = Depends(require_auth)):
    key = database.get_setting(DB_PATH, "gemini_api_key") or os.environ.get("GEMINI_API_KEY", "")
    masked_key = ""
    if key:
        masked_key = f"sk-...{key[-4:]}" if len(key) > 4 else "sk-configured"
    return {"has_key": bool(key), "masked_key": masked_key}


@app.post("/api/settings")
async def update_settings(payload: SettingsPayload, _user: dict = Depends(require_admin)):
    if not payload.gemini_api_key.strip():
        raise HTTPException(status_code=400, detail="API Key cannot be empty.")
    database.save_setting(DB_PATH, "gemini_api_key", payload.gemini_api_key.strip())
    return {"message": "Settings updated successfully."}


@app.get("/api/settings/email")
async def get_email_settings(_user: dict = Depends(require_auth)):
    return {
        "smtp_host":    database.get_setting(DB_PATH, "smtp_host", ""),
        "smtp_port":    int(database.get_setting(DB_PATH, "smtp_port", "587")),
        "smtp_user":    database.get_setting(DB_PATH, "smtp_user", ""),
        "from_name":    database.get_setting(DB_PATH, "smtp_from_name", "Daily AI Digest"),
        "enabled":      database.get_setting(DB_PATH, "email_enabled", "false").lower() == "true",
        "has_password": bool(database.get_setting(DB_PATH, "smtp_password", "")),
    }


@app.post("/api/settings/email")
async def update_email_settings(payload: EmailSettingsPayload, _user: dict = Depends(require_admin)):
    database.save_setting(DB_PATH, "smtp_host",      payload.smtp_host.strip())
    database.save_setting(DB_PATH, "smtp_port",      str(payload.smtp_port))
    database.save_setting(DB_PATH, "smtp_user",      payload.smtp_user.strip())
    database.save_setting(DB_PATH, "smtp_from_name", payload.from_name.strip())
    database.save_setting(DB_PATH, "email_enabled",  str(payload.enabled).lower())
    if payload.smtp_password.strip():
        database.save_setting(DB_PATH, "smtp_password", payload.smtp_password.strip())
    return {"message": "Email settings saved successfully."}


@app.get("/api/settings/scraper")
async def get_scraper_settings(_user: dict = Depends(require_auth)):
    return database.get_scraper_config(DB_PATH)


@app.post("/api/settings/scraper")
async def update_scraper_settings(payload: ScraperSettingsPayload, _user: dict = Depends(require_admin)):
    if not isinstance(payload.config, dict):
        raise HTTPException(status_code=400, detail="config must be a JSON object.")
    database.save_scraper_config(DB_PATH, payload.config)
    return {"message": "Scraper settings saved."}


@app.get("/api/settings/weekly-email")
async def get_weekly_email_settings(_user: dict = Depends(require_auth)):
    """Returns weekly email settings."""
    return {
        "enabled":    database.get_setting(DB_PATH, "weekly_email_enabled", "false").lower() == "true",
        "last_sent":  database.get_setting(DB_PATH, "last_weekly_email_sent", ""),
    }

class WeeklyEmailPayload(BaseModel):
    enabled: bool

@app.post("/api/settings/weekly-email")
async def update_weekly_email_settings(payload: WeeklyEmailPayload, _user: dict = Depends(require_admin)):
    """Enables or disables Sunday weekly digest email."""
    database.save_setting(DB_PATH, "weekly_email_enabled", str(payload.enabled).lower())
    return {"message": "Weekly email setting saved."}

@app.post("/api/email/weekly-test")
async def send_weekly_test(payload: TestEmailPayload, _user: dict = Depends(require_admin)):
    """Sends a test weekly digest email to the given address."""
    import re
    if not re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+", payload.to):
        raise HTTPException(status_code=400, detail="Invalid email address.")
    smtp_settings = get_smtp_settings()
    if not smtp_settings["host"] or not smtp_settings["user"]:
        raise HTTPException(status_code=400, detail="SMTP is not configured.")
    today    = datetime.now()
    start_dt = today - timedelta(days=6)
    digests  = database.get_digests_for_range(DB_PATH, start_dt.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d"))
    if not digests:
        raise HTTPException(status_code=404, detail="No digests available for the past 7 days.")
    week_label = f"{start_dt.strftime('%b %d')} – {today.strftime('%b %d, %Y')}"
    result = weekly_emailer.send_weekly_emails(
        smtp_settings,
        [{"email": payload.to.strip(), "name": "Test Recipient"}],
        digests, week_label
    )
    if result["sent"] > 0:
        return {"message": f"Weekly test email sent to {payload.to}.", "result": result}
    raise HTTPException(status_code=500, detail=f"Send failed: {'; '.join(result['errors'])}")


def validate_source_endpoint(url: str, source_type: str):
    import requests
    from bs4 import BeautifulSoup
    try:
        assert_public_http_url(url)
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return False, f"URL returned HTTP status {resp.status_code}"
        if source_type == "rss":
            try:
                soup = BeautifulSoup(resp.content, features="xml")
            except Exception:
                soup = BeautifulSoup(resp.content, "html.parser")
            items = soup.find_all("item") or soup.find_all("entry")
            if not items:
                rss_link = None
                for link in soup.find_all("link", rel="alternate"):
                    ltype = link.get("type", "")
                    if "rss" in ltype or "atom" in ltype or "xml" in ltype:
                        rss_link = link.get("href")
                        break
                if rss_link:
                    import urllib.parse
                    resolved = urllib.parse.urljoin(url, rss_link)
                    assert_public_http_url(resolved)
                    resp_rss = requests.get(resolved, headers=headers, timeout=10)
                    if resp_rss.status_code == 200:
                        try:
                            soup_rss = BeautifulSoup(resp_rss.content, features="xml")
                        except Exception:
                            soup_rss = BeautifulSoup(resp_rss.content, "html.parser")
                        if soup_rss.find_all("item") or soup_rss.find_all("entry"):
                            return True, "Valid RSS feed discovered via alternate link."
                return False, "No RSS/Atom <item> or <entry> tags found."
        return True, "Source validated successfully."
    except Exception as e:
        return False, f"Validation failed: {str(e)}"


@app.get("/api/sources")
async def list_sources(_user: dict = Depends(require_auth)):
    return database.get_sources(DB_PATH)


@app.post("/api/sources")
async def add_source(payload: SourcePayload, _user: dict = Depends(require_admin)):
    name  = payload.name.strip()
    url   = payload.url.strip()
    stype = payload.source_type.strip().lower()
    if not name or not url:
        raise HTTPException(status_code=400, detail="Name and URL are required.")
    if stype not in ["rss", "scrape"]:
        raise HTTPException(status_code=400, detail="Invalid source type.")
    import urllib.parse
    try:
        urllib.parse.urlparse(url)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid URL format.")
    valid, msg = validate_source_endpoint(url, stype)
    if not valid:
        raise HTTPException(status_code=422, detail=f"Source validation failed: {msg}")
    success = database.add_source(DB_PATH, name, url, stype, 1 if payload.enabled else 0)
    if not success:
        raise HTTPException(status_code=409, detail="A source with this URL already exists.")
    return {"message": f"Source '{name}' added successfully."}


@app.put("/api/sources/{id}")
async def update_source(id: int, payload: SourcePayload, _user: dict = Depends(require_admin)):
    name  = payload.name.strip()
    url   = payload.url.strip()
    stype = payload.source_type.strip().lower()
    if not name or not url:
        raise HTTPException(status_code=400, detail="Name and URL are required.")
    if stype not in ["rss", "scrape"]:
        raise HTTPException(status_code=400, detail="Invalid source type.")
    valid, msg = validate_source_endpoint(url, stype)
    if not valid:
        raise HTTPException(status_code=422, detail=f"Source validation failed: {msg}")
    success = database.update_source(DB_PATH, id, name, url, stype, 1 if payload.enabled else 0)
    if not success:
        raise HTTPException(status_code=404, detail="Source not found.")
    return {"message": f"Source '{name}' updated successfully."}


@app.delete("/api/sources/{id}")
async def delete_source(id: int, _user: dict = Depends(require_admin)):
    success = database.delete_source(DB_PATH, id)
    if not success:
        raise HTTPException(status_code=404, detail="Source not found.")
    return {"message": "Source deleted successfully."}


@app.post("/api/sources/{id}/toggle")
async def toggle_source(id: int, _user: dict = Depends(require_admin)):
    success, new_state = database.toggle_source(DB_PATH, id)
    if not success:
        raise HTTPException(status_code=404, detail="Source not found.")
    return {"message": "Source toggled.", "enabled": bool(new_state)}


@app.get("/api/subscribers")
async def list_subscribers(_user: dict = Depends(require_auth)):
    subs = database.get_all_subscribers(DB_PATH)
    return {"subscribers": subs}


@app.post("/api/subscribers")
async def add_subscriber(payload: SubscriberPayload, _user: dict = Depends(require_admin)):
    import re
    if not re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+", payload.email):
        raise HTTPException(status_code=400, detail="Invalid email address.")
    added = database.add_subscriber(DB_PATH, payload.email, payload.name)
    if not added:
        raise HTTPException(status_code=409, detail=f"{payload.email} is already subscribed.")
    return {"message": f"{payload.email} added successfully."}


@app.delete("/api/subscribers/{email:path}")
async def remove_subscriber(email: str, _user: dict = Depends(require_admin)):
    removed = database.remove_subscriber(DB_PATH, email)
    if not removed:
        raise HTTPException(status_code=404, detail="Subscriber not found.")
    return {"message": f"{email} removed successfully."}


@app.post("/api/email/test")
async def send_test_email(payload: TestEmailPayload, _user: dict = Depends(require_admin)):
    import re
    if not re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+", payload.to):
        raise HTTPException(status_code=400, detail="Invalid recipient email address.")
    smtp_settings = get_smtp_settings()
    if not smtp_settings["host"] or not smtp_settings["user"]:
        raise HTTPException(status_code=400, detail="SMTP is not configured.")
    target_date = payload.date.strip() if payload.date else datetime.now().strftime("%Y-%m-%d")
    digest_row  = database.get_digest(DB_PATH, target_date) or database.get_latest_digest(DB_PATH)
    if not digest_row:
        raise HTTPException(status_code=404, detail="No digest available to send.")
    recipients = [{"email": payload.to.strip(), "name": "Test Recipient"}]
    result = emailer.send_emails(smtp_settings, recipients, digest_row["content"], digest_row["date"])
    if result["sent"] > 0:
        return {"message": f"Test email sent to {payload.to}.", "result": result}
    raise HTTPException(status_code=500, detail=f"Send failed: {'; '.join(result['errors'])}")

# ---------------------------------------------------------------------------
# Bookmarks (per-user, auth required)
# ---------------------------------------------------------------------------

class BookmarkPayload(BaseModel):
    url: str
    title: str
    source: str = ""
    description: str = ""
    digest_date: str = ""

@app.get("/api/bookmarks")
async def get_bookmarks(current_user: dict = Depends(require_auth)):
    """Returns all bookmarks for the authenticated user."""
    return {"bookmarks": database.get_bookmarks(DB_PATH, current_user["id"])}

@app.post("/api/bookmarks")
async def add_bookmark(payload: BookmarkPayload, current_user: dict = Depends(require_auth)):
    """Bookmarks an article for the current user."""
    if not payload.url.strip():
        raise HTTPException(status_code=400, detail="URL is required.")
    added = database.add_bookmark(
        DB_PATH, current_user["id"],
        payload.url, payload.title, payload.source,
        payload.description, payload.digest_date
    )
    if not added:
        raise HTTPException(status_code=409, detail="Already bookmarked.")
    return {"message": "Bookmarked successfully."}

@app.delete("/api/bookmarks")
async def remove_bookmark(url: str, current_user: dict = Depends(require_auth)):
    """Removes a bookmark by URL for the current user."""
    removed = database.remove_bookmark(DB_PATH, current_user["id"], url)
    if not removed:
        raise HTTPException(status_code=404, detail="Bookmark not found.")
    return {"message": "Bookmark removed."}

@app.get("/api/bookmarks/urls")
async def get_bookmark_urls(current_user: dict = Depends(require_auth)):
    """Returns a list of all bookmarked URLs for fast client-side lookup."""
    return {"urls": list(database.get_bookmark_urls(DB_PATH, current_user["id"]))}


# ---------------------------------------------------------------------------
# Weekly digest email
# ---------------------------------------------------------------------------

def send_weekly_digest():
    """Compiles the past 7 days of digests and emails them as a Week-in-AI roundup."""
    try:
        enabled = database.get_setting(DB_PATH, "weekly_email_enabled", "false").lower() == "true"
        if not enabled:
            return

        subscribers = database.get_active_subscribers(DB_PATH)
        if not subscribers:
            logger.info("Weekly email: no active subscribers — skipping.")
            return

        smtp_settings = {
            "host":      database.get_setting(DB_PATH, "smtp_host", ""),
            "port":      int(database.get_setting(DB_PATH, "smtp_port", "587")),
            "user":      database.get_setting(DB_PATH, "smtp_user", ""),
            "password":  database.get_setting(DB_PATH, "smtp_password", ""),
            "from_name": database.get_setting(DB_PATH, "smtp_from_name", "Daily AI Digest"),
        }
        if not smtp_settings["host"] or not smtp_settings["user"]:
            logger.warning("Weekly email: SMTP not configured — skipping.")
            return

        today    = datetime.now()
        end_dt   = today
        start_dt = today - timedelta(days=6)
        start_str = start_dt.strftime("%Y-%m-%d")
        end_str   = end_dt.strftime("%Y-%m-%d")
        digests   = database.get_digests_for_range(DB_PATH, start_str, end_str)

        if not digests:
            logger.info("Weekly email: no digests found for the past 7 days — skipping.")
            return

        week_label = f"{start_dt.strftime('%b %d')} – {end_dt.strftime('%b %d, %Y')}"
        logger.info(f"Weekly email: sending to {len(subscribers)} subscriber(s) for {week_label}...")
        result = weekly_emailer.send_weekly_emails(smtp_settings, subscribers, digests, week_label)
        database.save_setting(DB_PATH, "last_weekly_email_sent", end_str)
        logger.info(f"Weekly email: sent={result['sent']}, failed={result['failed']}.")
        if result["errors"]:
            for err in result["errors"]:
                logger.error(f"Weekly email error: {err}")

    except Exception as exc:
        logger.error(f"send_weekly_digest failed: {exc}")


# ---------------------------------------------------------------------------
# User management (admin only)
# ---------------------------------------------------------------------------

class RolePayload(BaseModel):
    role: str  # "admin" or "viewer"

@app.get("/api/users")
async def list_users(_user: dict = Depends(require_admin)):
    """Returns all registered users (admin only)."""
    return {"users": database.get_all_users(DB_PATH)}

@app.patch("/api/users/{user_id}/role")
async def update_user_role(user_id: int, payload: RolePayload,
                            current_user: dict = Depends(require_admin)):
    """Promotes or demotes a user. Admins cannot demote themselves."""
    if payload.role not in ("admin", "viewer"):
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'viewer'.")
    if user_id == current_user["id"] and payload.role == "viewer":
        raise HTTPException(status_code=400, detail="You cannot demote yourself.")
    updated = database.update_user_role(DB_PATH, user_id, payload.role)
    if not updated:
        raise HTTPException(status_code=404, detail="User not found.")
    return {"message": f"User role updated to '{payload.role}'."}

# ---------------------------------------------------------------------------
# Scheduled email dispatch
# ---------------------------------------------------------------------------

def send_scheduled_emails(date_str):
    try:
        enabled = database.get_setting(DB_PATH, "email_enabled", "false").lower() == "true"
        if not enabled:
            return
        subscribers = database.get_active_subscribers(DB_PATH)
        if not subscribers:
            logger.info("Email scheduler: No active subscribers.")
            return
        smtp_settings = get_smtp_settings()
        if not smtp_settings["host"] or not smtp_settings["user"]:
            logger.warning("Email scheduler: SMTP not configured.")
            return
        digest_row = database.get_digest(DB_PATH, date_str)
        if not digest_row:
            logger.warning(f"Email scheduler: No digest for {date_str}.")
            return
        logger.info(f"Email scheduler: Dispatching to {len(subscribers)} subscriber(s)...")
        result = emailer.send_emails(smtp_settings, subscribers, digest_row["content"], date_str)
        database.save_setting(DB_PATH, "last_email_sent_date", date_str)
        add_log(f"Email delivery complete — sent: {result['sent']}, failed: {result['failed']}.")
        if result["errors"]:
            for err in result["errors"]:
                logger.error(f"Email error: {err}")
    except Exception as exc:
        logger.error(f"send_scheduled_emails failed: {exc}")

# ---------------------------------------------------------------------------
# Background hourly scheduler
# ---------------------------------------------------------------------------

def run_scheduled_tasks():
    """Runs one pass of the hourly job: auto-sync today's digest if missing,
    dispatch the daily email once per day after 7am, dispatch the weekly
    roundup on Sundays, and clean up expired sessions.

    Idempotent — safe to call from the in-process loop and the external cron
    trigger at the same time, since each step checks a "last done" marker
    before acting.
    """
    try:
        now       = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        row = database.get_digest(DB_PATH, today_str)

        if not row and SYNC_STATUS["status"] == "idle":
            logger.info(f"Auto-Scheduler: Today's digest ({today_str}) missing. Auto-triggering...")
            run_sync_job(today_str)

        if now.hour >= 7:
            # Daily digest email
            last_sent = database.get_setting(DB_PATH, "last_email_sent_date", "")
            if last_sent != today_str:
                send_scheduled_emails(today_str)

            # Weekly digest email — every Sunday (weekday 6)
            if now.weekday() == 6:
                last_weekly = database.get_setting(DB_PATH, "last_weekly_email_sent", "")
                if last_weekly != today_str:
                    send_weekly_digest()

        # Clean up expired sessions periodically
        database.delete_expired_sessions(DB_PATH)

    except Exception as e:
        logger.error(f"Error in scheduled tasks run: {e}")


def start_hourly_scheduler():
    """In-process fallback loop — only useful while the process happens to stay
    alive (e.g. always-on hosts, or a Render instance kept warm by traffic).
    On serverless environments like Vercel, rely on the external /api/cron/hourly
    trigger instead."""
    if os.environ.get("VERCEL") or os.environ.get("SERVERLESS") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        logger.info("Serverless environment detected (VERCEL). Skipping in-process daemon thread.")
        return

    def scheduler_loop():
        logger.info("Hourly background scheduler daemon started.")
        while True:
            run_scheduled_tasks()
            time.sleep(3600)

    thread = threading.Thread(target=scheduler_loop, daemon=True)
    thread.start()


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "127.0.0.1")
    logger.info(f"Starting server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
