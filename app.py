import os
import time
import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

# Configured first, before any other module-level `logging.basicConfig` call
# elsewhere in the codebase can install a conflicting plain-text handler.
from logging_config import configure_logging
configure_logging()
logger = logging.getLogger("app")

from fastapi import FastAPI, BackgroundTasks, HTTPException, Response, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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

# Shared secret for the external cron trigger (/api/cron/hourly) and the ops
# endpoints (/api/ops/*) — lets a free external pinger (cron-job.org etc.)
# drive the hourly job on hosts like Render's free tier, where the process
# sleeps and the in-process scheduler thread dies with it, and lets the local
# ops.py CLI trigger a sync / read status on a deployed instance.
CRON_SECRET = os.environ.get("CRON_SECRET", "")

# Where to send ops alerts (sync failures, all-sources-down) — unset by
# default, in which case alerts are just skipped (see send_alert_email).
ADMIN_ALERT_EMAIL = os.environ.get("ADMIN_ALERT_EMAIL", "")


def send_ops_alert(subject: str, body: str):
    if not ADMIN_ALERT_EMAIL:
        logger.warning(f"Ops alert suppressed (ADMIN_ALERT_EMAIL not set): {subject}")
        return
    smtp = get_smtp_settings()
    threading.Thread(
        target=emailer.send_alert_email,
        args=(smtp, ADMIN_ALERT_EMAIL, subject, body),
        daemon=True
    ).start()

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


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class SubscriberPayload(BaseModel):
    email: str
    name: str = ""

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
        failed_sources = []
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
                if not items:
                    failed_sources.append(name)
                add_log(f"-> {name}: Found {len(items)} items.")
                all_items.extend(items)

        # If every enabled source came back empty, that's almost certainly a
        # systemic problem (network egress blocked, all feeds down at once,
        # etc.) rather than coincidence — worth a heads-up rather than
        # silently publishing a digest built from zero fresh articles.
        if jobs and len(failed_sources) == len(jobs):
            send_ops_alert(
                f"All {len(jobs)} sources returned zero items ({date_str})",
                f"Every enabled source failed to return any items during today's sync:\n"
                f"{', '.join(failed_sources)}\n\n"
                f"This usually means a shared cause (network/DNS egress blocked, "
                f"all feeds simultaneously down, or a code change broke fetcher.py). "
                f"Today's digest will be built from whatever raw articles already "
                f"exist in the database, if any."
            )

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
        send_ops_alert(
            f"Sync job crashed ({date_str})",
            f"The synchronization job for {date_str} raised an unhandled exception:\n\n{e}\n\n"
            f"Recent log lines:\n" + "\n".join(SYNC_STATUS["logs"][-15:])
        )

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

def _fetch_og_image(url: str) -> dict:
    if url in _og_cache:
        return _og_cache[url]

    cached = database.get_cached_og_image(DB_PATH, url)
    if cached is not None:
        _og_cache[url] = cached
        return cached

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
                result["image_url"] = _urljoin_for_redirect(next_url, img_val)
                break
        if not result["image_url"]:
            link_tag = soup.find("link", {"rel": "image_src"})
            if link_tag and link_tag.get("href"):
                img_val = link_tag["href"].strip()
                if img_val.startswith("//"):
                    img_val = "https:" + img_val
                result["image_url"] = _urljoin_for_redirect(next_url, img_val)

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
    database.save_og_image_cache(DB_PATH, url, result["image_url"], result["title"])
    return result


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


def _check_ops_secret(request: Request):
    if not CRON_SECRET:
        raise HTTPException(status_code=503, detail="CRON_SECRET is not configured on the server.")
    provided = request.headers.get("X-Ops-Secret") or request.query_params.get("secret")
    if provided != CRON_SECRET:
        raise HTTPException(status_code=401, detail="Invalid or missing ops secret.")


@app.get("/api/ops/status")
async def ops_status(request: Request):
    """Read-only pipeline status for the ops.py CLI (or any other trusted script)
    to poll — same shared secret as /api/cron/hourly, since anyone who can
    trigger a sync should also be able to check on it."""
    _check_ops_secret(request)
    latest = database.get_latest_digest(DB_PATH)
    return {
        "sync": SYNC_STATUS,
        "latest_digest_date": (latest or {}).get("date"),
        "digest_count": len(database.get_all_digest_dates(DB_PATH)),
        "subscriber_count": len(database.get_active_subscribers(DB_PATH)),
        "source_count": len(database.get_sources(DB_PATH)),
        "admin_alert_email": ADMIN_ALERT_EMAIL or None,
    }


@app.post("/api/ops/sync")
async def ops_trigger_sync(request: Request, background_tasks: BackgroundTasks, date: str = None):
    """Manually triggers a sync run — the CLI-driven replacement for the old
    dashboard's 'Sync Latest News' button."""
    _check_ops_secret(request)
    if SYNC_STATUS["status"] in ["fetching", "analyzing"]:
        return JSONResponse(status_code=400, content={"detail": "A synchronization run is already active."})
    target_date = date or datetime.now().strftime("%Y-%m-%d")
    background_tasks.add_task(run_sync_job, target_date)
    return {"message": "Sync triggered.", "date": target_date}


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
