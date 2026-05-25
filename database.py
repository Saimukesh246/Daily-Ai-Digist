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

    conn.commit()
    conn.close()

def save_raw_article(db_path, date, source, title, description, url, category):
    """Saves a raw crawled article. Ignores duplicates based on URL."""
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
        # Article with this URL already exists
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
    """Retrieves all raw articles within a range prior to a base date (for comparisons)."""
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
