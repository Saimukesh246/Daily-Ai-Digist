import sqlite3
import json
import os
from datetime import datetime, timedelta

_DATA_DIR = os.environ.get("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(_DATA_DIR, "ai_digest.db")

def get_db_connection(db_path=DEFAULT_DB_PATH):
    """Establishes and returns a database connection."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(db_path=DEFAULT_DB_PATH):
    """Initializes the database schema if it doesn't already exist."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    # Table for storing raw crawled articles and news items
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS raw_articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        source TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT,
        url TEXT UNIQUE,
        category TEXT NOT NULL,
        fetched_at TEXT NOT NULL
    )
    """)

    # Table for storing completed generated digests
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS digests (
        date TEXT PRIMARY KEY,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """)

    # Table for storing persistent system settings
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """)

    # Table for email subscriber management
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS subscribers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        name TEXT DEFAULT '',
        active INTEGER DEFAULT 1,
        subscribed_at TEXT NOT NULL
    )
    """)

    # Table for dynamic news sources
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        url TEXT NOT NULL UNIQUE,
        source_type TEXT NOT NULL DEFAULT 'rss',
        enabled INTEGER DEFAULT 1,
        created_at TEXT NOT NULL
    )
    """)

    # --- AUTH TABLES ---

    # Table for registered users
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        name TEXT DEFAULT '',
        password_hash TEXT DEFAULT '',
        google_id TEXT DEFAULT '',
        avatar_url TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        last_login TEXT
    )
    """)

    # Table for active login sessions (JWT tokens)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """)

    # Seed default sources on first startup
    cursor.execute("SELECT COUNT(*) FROM sources")
    if cursor.fetchone()[0] == 0:
        now_str = datetime.utcnow().isoformat()
        cursor.executemany("""
        INSERT INTO sources (name, url, source_type, enabled, created_at)
        VALUES (?, ?, ?, ?, ?)
        """, [
            ("OpenAI Blog", "https://openai.com/news/rss/", "rss", 1, now_str),
            ("Google DeepMind Blog", "https://deepmind.google/blog/rss.xml", "rss", 1, now_str),
            ("Anthropic Blog", "https://www.anthropic.com/news", "scrape", 1, now_str)
        ])

    conn.commit()
    conn.close()

def save_raw_article(db_path, date, source, title, description, url, category):
    """Saves a raw crawled article. If URL already exists, updates the article details."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    fetched_at = datetime.utcnow().isoformat()
    try:
        cursor.execute("""
        INSERT INTO raw_articles (date, source, title, description, url, category, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (date, source, title, description, url, category, fetched_at))
        conn.commit()
        success = True
    except sqlite3.IntegrityError:
        cursor.execute("""
        UPDATE raw_articles
        SET date = ?, source = ?, title = ?, description = ?, category = ?, fetched_at = ?
        WHERE url = ?
        """, (date, source, title, description, category, fetched_at, url))
        conn.commit()
        success = False
    finally:
        conn.close()
    return success

def get_raw_articles_by_date(db_path, date):
    """Retrieves all raw articles for a specific date."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
    SELECT * FROM raw_articles WHERE date = ? ORDER BY id DESC
    """, (date,))
    rows = cursor.fetchall()
    articles = [dict(row) for row in rows]
    conn.close()
    return articles

def get_raw_articles_since(db_path, base_date_str, days=7):
    """Retrieves all raw articles within a range prior to a base date."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    base_date = datetime.strptime(base_date_str, "%Y-%m-%d")
    start_date = (base_date - timedelta(days=days)).strftime("%Y-%m-%d")
    cursor.execute("""
    SELECT * FROM raw_articles
    WHERE date >= ? AND date < ?
    ORDER BY date DESC, id DESC
    """, (start_date, base_date_str))
    rows = cursor.fetchall()
    articles = [dict(row) for row in rows]
    conn.close()
    return articles

def save_digest(db_path, date, content_dict):
    """Saves a compiled newsletter digest. Overwrites if already exists."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    content_json = json.dumps(content_dict, ensure_ascii=False)
    created_at = datetime.utcnow().isoformat()
    cursor.execute("""
    INSERT OR REPLACE INTO digests (date, content, created_at)
    VALUES (?, ?, ?)
    """, (date, content_json, created_at))
    conn.commit()
    conn.close()

def get_digest(db_path, date):
    """Retrieves the digest for a specific date."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
    SELECT * FROM digests WHERE date = ?
    """, (date,))
    row = cursor.fetchone()
    conn.close()
    if row:
        row_dict = dict(row)
        row_dict["content"] = json.loads(row_dict["content"])
        return row_dict
    return None

def get_latest_digest(db_path):
    """Retrieves the most recently generated digest."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
    SELECT * FROM digests ORDER BY date DESC LIMIT 1
    """)
    row = cursor.fetchone()
    conn.close()
    if row:
        row_dict = dict(row)
        row_dict["content"] = json.loads(row_dict["content"])
        return row_dict
    return None

def get_all_digest_dates(db_path):
    """Returns a list of all dates for which a digest has been generated."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
    SELECT date FROM digests ORDER BY date DESC
    """)
    rows = cursor.fetchall()
    dates = [row["date"] for row in rows]
    conn.close()
    return dates

def save_setting(db_path, key, value):
    """Saves a dynamic setting key-value pair."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
    INSERT OR REPLACE INTO settings (key, value)
    VALUES (?, ?)
    """, (key, str(value)))
    conn.commit()
    conn.close()

def get_setting(db_path, key, default=None):
    """Retrieves a dynamic setting value by key."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
    SELECT value FROM settings WHERE key = ?
    """, (key,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return row["value"]
    return default


# --- Subscriber management ---

def search_articles(db_path, query="", source_filter=None, limit=40):
    """Full-text search across raw article titles and descriptions."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    term = f"%{query}%"
    if source_filter:
        cursor.execute(
            """SELECT id, date, source, title, description, url, category
               FROM raw_articles
               WHERE (title LIKE ? OR description LIKE ?) AND source = ?
               ORDER BY date DESC, id DESC LIMIT ?""",
            (term, term, source_filter, limit),
        )
    else:
        cursor.execute(
            """SELECT id, date, source, title, description, url, category
               FROM raw_articles
               WHERE title LIKE ? OR description LIKE ?
               ORDER BY date DESC, id DESC LIMIT ?""",
            (term, term, limit),
        )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_distinct_sources(db_path):
    """Returns all distinct source names present in raw_articles."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT source FROM raw_articles ORDER BY source")
    rows = cursor.fetchall()
    conn.close()
    return [row["source"] for row in rows]


def add_subscriber(db_path, email, name=""):
    """Adds a new subscriber. Returns True if added, False if email already exists."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    subscribed_at = datetime.utcnow().isoformat()
    try:
        cursor.execute(
            "INSERT INTO subscribers (email, name, active, subscribed_at) VALUES (?, ?, 1, ?)",
            (email.strip().lower(), name.strip(), subscribed_at),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def remove_subscriber(db_path, email):
    """Removes a subscriber by email. Returns True if a row was deleted."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM subscribers WHERE email = ?", (email.strip().lower(),))
    removed = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return removed


def get_all_subscribers(db_path):
    """Returns all subscribers (active and inactive) as a list of dicts."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, email, name, active, subscribed_at FROM subscribers ORDER BY subscribed_at DESC"
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_active_subscribers(db_path):
    """Returns only active subscribers as a list of dicts with 'email' and 'name' keys."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT email, name FROM subscribers WHERE active = 1 ORDER BY subscribed_at"
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


# --- Scraper configuration ---

DEFAULT_SCRAPER_CONFIG = {
    "hacker_news":  {"enabled": True,  "limit": 20},
    # Fixed typo: "ArtificialInteligence" → "ArtificialIntelligence"
    "reddit":       {"enabled": True,  "subreddits": ["MachineLearning", "singularity", "ArtificialIntelligence"], "limit": 10},
    "huggingface":  {"enabled": True,  "limit": 15},
    "arxiv":        {"enabled": True,  "limit": 15},
    "github":       {"enabled": True,  "keywords": ["ai", "llm", "agent", "machine-learning", "neural"], "limit": 15},
    "product_hunt": {"enabled": True},
    "lab_blogs":    {"enabled": True},
    "custom_rss":   [],
}


def get_scraper_config(db_path):
    """Returns the current scraper config merged with defaults for any missing keys."""
    raw = get_setting(db_path, "scraper_config", None)
    if raw:
        try:
            stored = json.loads(raw)
            import copy
            config = copy.deepcopy(DEFAULT_SCRAPER_CONFIG)
            for key, val in stored.items():
                if key in config:
                    if isinstance(config[key], dict) and isinstance(val, dict):
                        config[key].update(val)
                    else:
                        config[key] = val
                else:
                    config[key] = val
            return config
        except Exception:
            pass
    import copy
    return copy.deepcopy(DEFAULT_SCRAPER_CONFIG)


def save_scraper_config(db_path, config):
    """Saves the scraper configuration dict to the settings table as JSON."""
    save_setting(db_path, "scraper_config", json.dumps(config))


# --- Dynamic Sources Management ---

def add_source(db_path, name, url, source_type="rss", enabled=1):
    """Adds a new dynamic news/blog source. Returns True if successful, False if URL duplicate."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    created_at = datetime.utcnow().isoformat()
    try:
        cursor.execute("""
        INSERT INTO sources (name, url, source_type, enabled, created_at)
        VALUES (?, ?, ?, ?, ?)
        """, (name.strip(), url.strip(), source_type, enabled, created_at))
        conn.commit()
        success = True
    except sqlite3.IntegrityError:
        success = False
    finally:
        conn.close()
    return success


def get_sources(db_path):
    """Retrieves all dynamic news/blog sources."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM sources ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_source(db_path, source_id):
    """Retrieves a specific news/blog source by ID."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM sources WHERE id = ?", (source_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def update_source(db_path, source_id, name, url, source_type, enabled):
    """Updates a dynamic news/blog source details."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE sources
        SET name = ?, url = ?, source_type = ?, enabled = ?
        WHERE id = ?
        """, (name.strip(), url.strip(), source_type, int(enabled), source_id))
        conn.commit()
        success = cursor.rowcount > 0
    except sqlite3.IntegrityError:
        success = False
    finally:
        conn.close()
    return success


def delete_source(db_path, source_id):
    """Deletes a dynamic news/blog source."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    success = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return success


def toggle_source(db_path, source_id):
    """Toggles the enabled status of a news/blog source. Returns (success, new_state)."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT enabled FROM sources WHERE id = ?", (source_id,))
    row = cursor.fetchone()
    if row:
        new_state = 0 if row["enabled"] else 1
        cursor.execute("UPDATE sources SET enabled = ? WHERE id = ?", (new_state, source_id))
        conn.commit()
        success = True
    else:
        success = False
        new_state = None
    conn.close()
    return success, new_state


# =============================================================================
# AUTH — Users & Sessions
# =============================================================================

def create_user(db_path, email, name="", password_hash="", google_id="", avatar_url=""):
    """Creates a new user. Returns user dict on success, None if email already exists."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    created_at = datetime.utcnow().isoformat()
    try:
        cursor.execute("""
        INSERT INTO users (email, name, password_hash, google_id, avatar_url, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (email.strip().lower(), name.strip(), password_hash, google_id, avatar_url, created_at))
        conn.commit()
        user_id = cursor.lastrowid
        conn.close()
        return get_user_by_id(db_path, user_id)
    except sqlite3.IntegrityError:
        conn.close()
        return None


def get_user_by_email(db_path, email):
    """Retrieves a user by email address."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_id(db_path, user_id):
    """Retrieves a user by ID."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_google_id(db_path, google_id):
    """Retrieves a user by their Google account ID."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE google_id = ?", (google_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def update_user_last_login(db_path, user_id):
    """Stamps the user's last_login timestamp to now."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET last_login = ? WHERE id = ?",
                   (datetime.utcnow().isoformat(), user_id))
    conn.commit()
    conn.close()


def update_user_google_info(db_path, user_id, google_id, avatar_url, name):
    """Updates Google OAuth info for an existing user."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
    UPDATE users SET google_id = ?, avatar_url = ?, name = ?
    WHERE id = ?
    """, (google_id, avatar_url, name, user_id))
    conn.commit()
    conn.close()


def get_user_count(db_path):
    """Returns total number of registered users."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM users")
    row = cursor.fetchone()
    conn.close()
    return row["cnt"] if row else 0


def save_session(db_path, token, user_id, expires_hours=720):
    """Stores a new session token. Default expiry: 30 days (720 hours)."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    created_at = datetime.utcnow().isoformat()
    expires_at = (datetime.utcnow() + timedelta(hours=expires_hours)).isoformat()
    cursor.execute("""
    INSERT OR REPLACE INTO sessions (token, user_id, created_at, expires_at)
    VALUES (?, ?, ?, ?)
    """, (token, user_id, created_at, expires_at))
    conn.commit()
    conn.close()


def get_session(db_path, token):
    """Retrieves a session by token. Returns None if expired or not found."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM sessions WHERE token = ?", (token,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    session = dict(row)
    # Check expiry
    try:
        expires_at = datetime.fromisoformat(session["expires_at"])
        if datetime.utcnow() > expires_at:
            delete_session(db_path, token)
            return None
    except Exception:
        pass
    return session


def delete_session(db_path, token):
    """Removes a specific session (logout)."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()


def delete_expired_sessions(db_path):
    """Cleans up all expired sessions."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    now = datetime.utcnow().isoformat()
    cursor.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
    conn.commit()
    conn.close()
