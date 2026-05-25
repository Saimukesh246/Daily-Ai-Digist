import os
import time
import logging
import threading
from datetime import datetime
from fastapi import FastAPI, BackgroundTasks, HTTPException, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import database
import fetcher
import analyzer
import emailer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

# Initialize FastAPI application
app = FastAPI(title="Daily AI Digest Server", version="1.0.0")

# Setup folder directories
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(os.path.join(STATIC_DIR, "css"), exist_ok=True)
os.makedirs(os.path.join(STATIC_DIR, "js"), exist_ok=True)

# Mount the static files directory
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Database initialization
DB_PATH = database.DEFAULT_DB_PATH
database.init_db(DB_PATH)

# Global synchronization progress state
SYNC_STATUS = {
    "status": "idle",  # "idle", "fetching", "analyzing", "complete", "error"
    "current_step": "System ready",
    "logs": ["Server initialized."],
    "error_message": "",
    "completed_at": None
}

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

def add_log(message):
    """Utility to log messages both to the console and to the global status feed."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    log_line = f"[{timestamp}] {message}"
    logger.info(message)
    SYNC_STATUS["logs"].append(log_line)
    SYNC_STATUS["current_step"] = message

def run_sync_job(date_str):
    """Executes the complete fetch-and-analyze loop in a background thread."""
    global SYNC_STATUS
    try:
        SYNC_STATUS["status"] = "fetching"
        SYNC_STATUS["error_message"] = ""
        SYNC_STATUS["completed_at"] = None
        SYNC_STATUS["logs"] = []
        
        add_log("Starting Daily AI Digest gathering job...")
        
        # 1. Fetching raw resources
        add_log("Connecting to data sources...")
        add_log("Crawling Hacker News AI stories (last 36 hours)...")
        hn_items = fetcher.fetch_hacker_news_ai(date_str)
        add_log(f"-> Hacker News: Found {len(hn_items)} articles.")
        
        add_log("Crawling Reddit AI subreddits (r/MachineLearning, r/singularity, r/ArtificialInteligence)...")
        reddit_items = fetcher.fetch_reddit_ai()
        add_log(f"-> Reddit: Found {len(reddit_items)} posts.")
        
        add_log("Crawling Hugging Face daily paper API...")
        hf_items = fetcher.fetch_huggingface_papers()
        add_log(f"-> Hugging Face: Found {len(hf_items)} papers.")
        
        add_log("Crawling Arxiv CS.AI XML feed...")
        arxiv_items = fetcher.fetch_arxiv_ai()
        add_log(f"-> Arxiv: Found {len(arxiv_items)} preprints.")
        
        add_log("Crawling GitHub API for trending AI repositories (created in last 7 days)...")
        github_items = fetcher.fetch_github_trending()
        add_log(f"-> GitHub Trending: Found {len(github_items)} repositories.")
        
        add_log("Crawling Product Hunt tech launch RSS feed...")
        ph_items = fetcher.fetch_product_hunt_ai()
        add_log(f"-> Product Hunt: Found {len(ph_items)} launches.")
        
        add_log("Crawling AI Lab blogs (OpenAI, DeepMind, Anthropic news)...")
        lab_items = fetcher.fetch_lab_blogs()
        add_log(f"-> Lab Blogs: Found {len(lab_items)} articles.")
        
        # Combine and deduplicate
        all_items = hn_items + reddit_items + hf_items + arxiv_items + github_items + ph_items + lab_items
        unique_items = []
        seen_urls = set()
        for item in all_items:
            url = item.get("url")
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique_items.append(item)
                
        add_log(f"Deduplication completed. Total unique entries: {len(unique_items)}")
        
        # Save raw articles to DB
        add_log("Saving raw articles to local SQLite database...")
        saved_count = 0
        for item in unique_items:
            saved = database.save_raw_article(
                DB_PATH, date_str, item["source"], item["title"],
                item["description"], item["url"], item["category"]
            )
            if saved:
                saved_count += 1
        add_log(f"-> Saved {saved_count} new entries. (Others were skipped as duplicates)")
        
        # 2. News synthesis
        SYNC_STATUS["status"] = "analyzing"
        add_log("Entering Synthesis stage. Preparing context package...")
        
        # Retrieve settings or fallback environment variables
        api_key = database.get_setting(DB_PATH, "gemini_api_key") or os.environ.get("GEMINI_API_KEY")
        if api_key:
            add_log("Gemini API key detected. Initiating AI synthesis model (gemini-1.5-flash)...")
        else:
            add_log("No Gemini API key configured. Activating local intelligent offline fallback engine...")
            
        digest, mode = analyzer.generate_digest(DB_PATH, date_str, api_key)
        
        if "fallback" in mode:
            add_log("Intelligent offline synthesis complete (using heuristic template compilation).")
        else:
            add_log("Gemini synthesis completed successfully! Structured JSON generated and verified.")
            
        SYNC_STATUS["status"] = "complete"
        SYNC_STATUS["completed_at"] = datetime.now().isoformat()
        add_log(f"Successfully compiled Daily AI Digest for {date_str}!")
        
    except Exception as e:
        logger.exception("Synchronization job failed:")
        SYNC_STATUS["status"] = "error"
        SYNC_STATUS["error_message"] = str(e)
        add_log(f"CRITICAL ERROR: {e}")

# --- WEB ENDPOINTS ---

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Serves the dashboard index file directly."""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(index_path):
        # Serve a helpful fallback loading message if frontend files aren't created yet
        return """
        <html>
            <head><title>Daily AI Digest</title><style>body {background:#0f172a; color:#f8fafc; font-family:sans-serif; text-align:center; padding:100px;}</style></head>
            <body><h1>Daily AI Digest Engine Online</h1><p>The static frontend is being generated. Please refresh in a moment...</p></body>
        </html>
        """
    with open(index_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/api/digests")
async def list_digests():
    """Returns a list of all dates that have compiled digests."""
    dates = database.get_all_digest_dates(DB_PATH)
    return {"dates": dates}

@app.get("/api/digests/latest")
async def get_latest_digest():
    """Retrieves the newest digest available."""
    digest = database.get_latest_digest(DB_PATH)
    if not digest:
        raise HTTPException(status_code=404, detail="No digests generated yet.")
    return digest

@app.get("/api/digests/{date}")
async def get_digest_by_date(date: str):
    """Retrieves a digest by specific date (format YYYY-MM-DD)."""
    digest = database.get_digest(DB_PATH, date)
    if not digest:
        raise HTTPException(status_code=404, detail=f"Digest for date {date} not found.")
    return digest

@app.post("/api/trigger")
async def trigger_sync(background_tasks: BackgroundTasks, date: str = None):
    """Triggers an asynchronous fetch and compile run."""
    global SYNC_STATUS
    if SYNC_STATUS["status"] in ["fetching", "analyzing"]:
        return JSONResponse(status_code=400, content={"detail": "A synchronization run is already actively running."})
        
    target_date = date or datetime.now().strftime("%Y-%m-%d")
    background_tasks.add_task(run_sync_job, target_date)
    return {"message": "Aggregation background task triggered successfully.", "date": target_date}

@app.get("/api/status")
async def get_status():
    """Returns the current running status and full log lines of the crawler engine."""
    return SYNC_STATUS

@app.get("/api/settings")
async def get_settings():
    """Retrieves system configurations (masking the API key for security)."""
    key = database.get_setting(DB_PATH, "gemini_api_key") or os.environ.get("GEMINI_API_KEY", "")
    masked_key = ""
    if key:
        masked_key = f"sk-...{key[-4:]}" if len(key) > 4 else "sk-configured"
    return {"has_key": bool(key), "masked_key": masked_key}

@app.post("/api/settings")
async def update_settings(payload: SettingsPayload):
    """Saves a new Gemini API Key to SQLite settings."""
    if not payload.gemini_api_key.strip():
        raise HTTPException(status_code=400, detail="API Key cannot be empty.")
    database.save_setting(DB_PATH, "gemini_api_key", payload.gemini_api_key.strip())
    return {"message": "Settings updated successfully."}

# --- Email settings endpoints ---

@app.get("/api/settings/email")
async def get_email_settings():
    """Returns current SMTP configuration (password is never returned)."""
    return {
        "smtp_host":    database.get_setting(DB_PATH, "smtp_host", ""),
        "smtp_port":    int(database.get_setting(DB_PATH, "smtp_port", "587")),
        "smtp_user":    database.get_setting(DB_PATH, "smtp_user", ""),
        "from_name":    database.get_setting(DB_PATH, "smtp_from_name", "Daily AI Digest"),
        "enabled":      database.get_setting(DB_PATH, "email_enabled", "false").lower() == "true",
        "has_password": bool(database.get_setting(DB_PATH, "smtp_password", "")),
    }

@app.post("/api/settings/email")
async def update_email_settings(payload: EmailSettingsPayload):
    """Saves SMTP configuration. Skips overwriting password if the field is blank."""
    database.save_setting(DB_PATH, "smtp_host",     payload.smtp_host.strip())
    database.save_setting(DB_PATH, "smtp_port",     str(payload.smtp_port))
    database.save_setting(DB_PATH, "smtp_user",     payload.smtp_user.strip())
    database.save_setting(DB_PATH, "smtp_from_name", payload.from_name.strip())
    database.save_setting(DB_PATH, "email_enabled", str(payload.enabled).lower())
    if payload.smtp_password.strip():
        database.save_setting(DB_PATH, "smtp_password", payload.smtp_password.strip())
    return {"message": "Email settings saved successfully."}

# --- Subscriber endpoints ---

@app.get("/api/subscribers")
async def list_subscribers():
    """Returns all registered email subscribers."""
    subs = database.get_all_subscribers(DB_PATH)
    return {"subscribers": subs}

@app.post("/api/subscribers")
async def add_subscriber(payload: SubscriberPayload):
    """Adds a new subscriber. Rejects duplicates and obviously invalid addresses."""
    import re
    if not re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+", payload.email):
        raise HTTPException(status_code=400, detail="Invalid email address.")
    added = database.add_subscriber(DB_PATH, payload.email, payload.name)
    if not added:
        raise HTTPException(status_code=409, detail=f"{payload.email} is already subscribed.")
    return {"message": f"{payload.email} added successfully."}

@app.delete("/api/subscribers/{email:path}")
async def remove_subscriber(email: str):
    """Removes a subscriber by email address."""
    removed = database.remove_subscriber(DB_PATH, email)
    if not removed:
        raise HTTPException(status_code=404, detail="Subscriber not found.")
    return {"message": f"{email} removed successfully."}

# --- Test email endpoint ---

@app.post("/api/email/test")
async def send_test_email(payload: TestEmailPayload):
    """Sends a one-off test digest email to the provided address."""
    import re
    if not re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+", payload.to):
        raise HTTPException(status_code=400, detail="Invalid recipient email address.")

    smtp_settings = {
        "host":      database.get_setting(DB_PATH, "smtp_host", ""),
        "port":      int(database.get_setting(DB_PATH, "smtp_port", "587")),
        "user":      database.get_setting(DB_PATH, "smtp_user", ""),
        "password":  database.get_setting(DB_PATH, "smtp_password", ""),
        "from_name": database.get_setting(DB_PATH, "smtp_from_name", "Daily AI Digest"),
    }
    if not smtp_settings["host"] or not smtp_settings["user"]:
        raise HTTPException(status_code=400, detail="SMTP is not configured. Save your SMTP settings first.")

    target_date = payload.date.strip() if payload.date else datetime.now().strftime("%Y-%m-%d")
    digest_row  = database.get_digest(DB_PATH, target_date) or database.get_latest_digest(DB_PATH)
    if not digest_row:
        raise HTTPException(status_code=404, detail="No digest available to send. Run a sync first.")

    recipients = [{"email": payload.to.strip(), "name": "Test Recipient"}]
    result = emailer.send_emails(smtp_settings, recipients, digest_row["content"], digest_row["date"])

    if result["sent"] > 0:
        return {"message": f"Test email sent to {payload.to}.", "result": result}
    raise HTTPException(status_code=500, detail=f"Send failed: {'; '.join(result['errors'])}")

# --- EMAIL DISPATCH ---

def send_scheduled_emails(date_str):
    """
    Sends the digest to all active subscribers if email delivery is enabled.
    Tracks the last sent date in settings to avoid sending duplicates.
    """
    try:
        enabled = database.get_setting(DB_PATH, "email_enabled", "false").lower() == "true"
        if not enabled:
            return

        subscribers = database.get_active_subscribers(DB_PATH)
        if not subscribers:
            logger.info("Email scheduler: No active subscribers — skipping delivery.")
            return

        smtp_settings = {
            "host":      database.get_setting(DB_PATH, "smtp_host", ""),
            "port":      int(database.get_setting(DB_PATH, "smtp_port", "587")),
            "user":      database.get_setting(DB_PATH, "smtp_user", ""),
            "password":  database.get_setting(DB_PATH, "smtp_password", ""),
            "from_name": database.get_setting(DB_PATH, "smtp_from_name", "Daily AI Digest"),
        }
        if not smtp_settings["host"] or not smtp_settings["user"]:
            logger.warning("Email scheduler: SMTP not configured — skipping delivery.")
            return

        digest_row = database.get_digest(DB_PATH, date_str)
        if not digest_row:
            logger.warning(f"Email scheduler: No digest for {date_str} — skipping.")
            return

        logger.info(f"Email scheduler: Dispatching to {len(subscribers)} subscriber(s)...")
        result = emailer.send_emails(
            smtp_settings, subscribers, digest_row["content"], date_str
        )
        database.save_setting(DB_PATH, "last_email_sent_date", date_str)
        add_log(f"Email delivery complete — sent: {result['sent']}, failed: {result['failed']}.")
        if result["errors"]:
            for err in result["errors"]:
                logger.error(f"Email scheduler error: {err}")

    except Exception as exc:
        logger.error(f"send_scheduled_emails failed: {exc}")


# --- BACKGROUND HOURLY SCHEDULER ---

def start_hourly_scheduler():
    """Runs a background daemon thread that performs an hourly check for the daily digest."""
    def scheduler_loop():
        logger.info("Hourly background scheduler daemon started.")
        while True:
            try:
                now       = datetime.now()
                today_str = now.strftime("%Y-%m-%d")

                # Check if today's digest exists
                conn = database.get_db_connection(DB_PATH)
                cursor = conn.cursor()
                cursor.execute("SELECT date FROM digests WHERE date = ?", (today_str,))
                row = cursor.fetchone()
                conn.close()

                # If it doesn't exist and the system is idle, trigger auto-generation
                if not row and SYNC_STATUS["status"] == "idle":
                    logger.info(f"Auto-Scheduler: Today's digest ({today_str}) is missing. Auto-triggering run...")
                    run_sync_job(today_str)

                # Send scheduled emails at or after 7 AM if not yet sent today
                if now.hour >= 7:
                    last_sent = database.get_setting(DB_PATH, "last_email_sent_date", "")
                    if last_sent != today_str:
                        send_scheduled_emails(today_str)

            except Exception as e:
                logger.error(f"Error in background scheduler: {e}")

            # Sleep for 1 hour (3600 seconds)
            time.sleep(3600)

    thread = threading.Thread(target=scheduler_loop, daemon=True)
    thread.start()

# Trigger the hourly check on startup
@app.on_event("startup")
async def on_startup():
    start_hourly_scheduler()

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    host = "0.0.0.0" if os.environ.get("RAILWAY_ENVIRONMENT") else "127.0.0.1"
    logger.info(f"Starting server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
