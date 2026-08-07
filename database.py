import json
import os
from datetime import datetime, timedelta

import psycopg2
import psycopg2.extras
from psycopg2 import pool as pg_pool
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def _strip_unsupported_dsn_params(url):
    """libpq doesn't understand pgbouncer=... query params some hosts add to pooled URLs."""
    parts = urlsplit(url)
    params = [(k, v) for k, v in parse_qsl(parts.query) if k != "pgbouncer"]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment))


_DSN = _strip_unsupported_dsn_params(DATABASE_URL) if DATABASE_URL else None
_pool = pg_pool.ThreadedConnectionPool(1, 10, dsn=_DSN) if _DSN else None

# Kept for backward compatibility with callers that still pass a db_path —
# the Postgres connection comes from DATABASE_URL instead, so this is unused.
DEFAULT_DB_PATH = None


def get_db_connection(db_path=None):
    """Borrows a pooled connection, discarding it and retrying once if Supabase's
    pgbouncer has silently closed it server-side after being idle in the pool."""
    if _pool is None:
        raise RuntimeError("DATABASE_URL environment variable is not set. Please check your .env file.")
    conn = _pool.getconn()
    try:
        conn.cursor().execute("SELECT 1")
    except psycopg2.OperationalError:
        _pool.putconn(conn, close=True)
        conn = _pool.getconn()
    return conn


def release_db_connection(conn):
    if _pool is not None and conn is not None:
        _pool.putconn(conn)


def _dict_cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def init_db(db_path=None):
    """Initializes the database schema if it doesn't already exist."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS raw_articles (
            id SERIAL PRIMARY KEY,
            date TEXT NOT NULL,
            source TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            url TEXT UNIQUE,
            category TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS digests (
            date TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS subscribers (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            name TEXT DEFAULT '',
            active INTEGER DEFAULT 1,
            subscribed_at TEXT NOT NULL
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS sources (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL UNIQUE,
            source_type TEXT NOT NULL DEFAULT 'rss',
            enabled INTEGER DEFAULT 1,
            created_at TEXT NOT NULL
        )
        """)

        # --- AUTH TABLES ---

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            name TEXT DEFAULT '',
            password_hash TEXT DEFAULT '',
            google_id TEXT DEFAULT '',
            avatar_url TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            last_login TEXT,
            role TEXT DEFAULT 'viewer'
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS bookmarks (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            url TEXT NOT NULL,
            title TEXT NOT NULL,
            source TEXT DEFAULT '',
            description TEXT DEFAULT '',
            digest_date TEXT DEFAULT '',
            bookmarked_at TEXT NOT NULL,
            UNIQUE(user_id, url),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """)

        conn.commit()

        # Master list of curated, verified-working AI/ML blog sources
        MASTER_SOURCES = [
            ("OpenAI Blog",            "https://openai.com/blog/rss.xml",                           "rss",    1),
            ("Anthropic Blog",         "https://www.anthropic.com/news",                             "scrape", 1),
            ("Google DeepMind Blog",   "https://deepmind.google/blog/rss.xml",                      "rss",    1),
            ("Google AI Blog",         "https://blog.google/technology/ai/rss/",                    "rss",    1),
            ("Hugging Face Blog",      "https://huggingface.co/blog/feed.xml",                      "rss",    1),
            ("NVIDIA AI Blog",         "https://blogs.nvidia.com/blog/category/deep-learning/feed/","rss",    1),
            ("AWS ML Blog",            "https://aws.amazon.com/blogs/machine-learning/feed/",       "rss",    1),
            ("Berkeley AI Research",   "https://bair.berkeley.edu/blog/feed.xml",                   "rss",    1),
            ("Distill.pub",            "https://distill.pub/rss.xml",                               "rss",    1),
            ("The Gradient",           "https://thegradient.pub/rss/",                              "rss",    1),
            ("AI Alignment Forum",     "https://www.alignmentforum.org/feed.xml",                   "rss",    1),
            ("Import AI (Jack Clark)",  "https://jack-clark.net/feed/",                             "rss",    1),
            ("Last Week in AI",         "https://lastweekin.ai/feed",                               "rss",    1),
            ("Lilian Weng Blog",        "https://lilianweng.github.io/index.xml",                   "rss",    1),
            ("Andrej Karpathy",         "https://karpathy.substack.com/feed",                       "rss",    1),
            ("Sebastian Raschka",       "https://magazine.sebastianraschka.com/feed",               "rss",    1),
            ("Simon Willison",          "https://simonwillison.net/atom/everything/",                "rss",    1),
            ("Jay Alammar Blog",        "https://jalammar.github.io/feed.xml",                      "rss",    1),
            ("LessWrong (AI curated)",  "https://www.lesswrong.com/feed.xml?view=curated-rss",      "rss",    1),
            ("MIT Technology Review",  "https://www.technologyreview.com/feed/",                    "rss",    1),
            ("Ars Technica AI",        "https://feeds.arstechnica.com/arstechnica/technology-lab",  "rss",    1),
            ("VentureBeat AI",         "https://venturebeat.com/category/ai/feed/",                 "rss",    1),
            ("TechCrunch AI",          "https://techcrunch.com/category/artificial-intelligence/feed/","rss", 1),
            ("The Verge AI",           "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml","rss",1),
            ("InfoQ AI & ML",          "https://feed.infoq.com/ai-ml-data-eng/",                   "rss",    1),
            ("Replicate Blog",         "https://replicate.com/blog/rss",                            "rss",    1),
            ("Lex Fridman",            "https://lexfridman.com/feed/",                              "rss",    0),
        ]

        now_str = datetime.utcnow().isoformat()
        cursor.executemany("""
        INSERT INTO sources (name, url, source_type, enabled, created_at)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (url) DO NOTHING
        """, [(n, u, t, e, now_str) for n, u, t, e in MASTER_SOURCES])

        conn.commit()
    finally:
        release_db_connection(conn)


def save_raw_article(db_path, date, source, title, description, url, category):
    """Saves a raw crawled article. If URL already exists, updates the article details."""
    conn = get_db_connection()
    fetched_at = datetime.utcnow().isoformat()
    try:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO raw_articles (date, source, title, description, url, category, fetched_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (url) DO UPDATE SET
            date = EXCLUDED.date,
            source = EXCLUDED.source,
            title = EXCLUDED.title,
            description = EXCLUDED.description,
            category = EXCLUDED.category,
            fetched_at = EXCLUDED.fetched_at
        RETURNING (xmax = 0) AS inserted
        """, (date, source, title, description, url, category, fetched_at))
        row = cursor.fetchone()
        conn.commit()
        return bool(row[0]) if row else False
    finally:
        release_db_connection(conn)


def save_raw_articles_bulk(db_path, date, items):
    """Saves many raw articles in a single round-trip/transaction. Returns count of newly inserted rows."""
    if not items:
        return 0
    conn = get_db_connection()
    try:
        fetched_at = datetime.utcnow().isoformat()
        rows = [
            (date, item["source"], item["title"], item["description"], item["url"], item["category"], fetched_at)
            for item in items
        ]
        cursor = conn.cursor()
        inserted = psycopg2.extras.execute_values(
            cursor,
            """
            INSERT INTO raw_articles (date, source, title, description, url, category, fetched_at)
            VALUES %s
            ON CONFLICT (url) DO UPDATE SET
                date = EXCLUDED.date,
                source = EXCLUDED.source,
                title = EXCLUDED.title,
                description = EXCLUDED.description,
                category = EXCLUDED.category,
                fetched_at = EXCLUDED.fetched_at
            RETURNING (xmax = 0) AS inserted
            """,
            rows,
            fetch=True,
        )
        conn.commit()
        return sum(1 for r in inserted if r[0])
    finally:
        release_db_connection(conn)


def get_raw_articles_by_date(db_path, date):
    """Retrieves all raw articles for a specific date, newest first."""
    conn = get_db_connection()
    try:
        cursor = _dict_cursor(conn)
        cursor.execute("""
        SELECT * FROM raw_articles WHERE date = %s ORDER BY fetched_at DESC, id DESC
        """, (date,))
        return [dict(row) for row in cursor.fetchall()]
    finally:
        release_db_connection(conn)


def clear_articles_for_date(db_path, date):
    """Deletes all raw articles for a date so the next sync fetches fully fresh content."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM raw_articles WHERE date = %s", (date,))
        deleted = cursor.rowcount
        conn.commit()
        return deleted
    finally:
        release_db_connection(conn)


def get_raw_articles_since(db_path, base_date_str, days=7):
    """Retrieves all raw articles within a range prior to a base date."""
    conn = get_db_connection()
    try:
        cursor = _dict_cursor(conn)
        base_date = datetime.strptime(base_date_str, "%Y-%m-%d")
        start_date = (base_date - timedelta(days=days)).strftime("%Y-%m-%d")
        cursor.execute("""
        SELECT * FROM raw_articles
        WHERE date >= %s AND date < %s
        ORDER BY date DESC, id DESC
        """, (start_date, base_date_str))
        return [dict(row) for row in cursor.fetchall()]
    finally:
        release_db_connection(conn)


def save_digest(db_path, date, content_dict):
    """Saves a compiled newsletter digest. Overwrites if already exists."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        content_json = json.dumps(content_dict, ensure_ascii=False)
        created_at = datetime.utcnow().isoformat()
        cursor.execute("""
        INSERT INTO digests (date, content, created_at)
        VALUES (%s, %s, %s)
        ON CONFLICT (date) DO UPDATE SET
            content = EXCLUDED.content,
            created_at = EXCLUDED.created_at
        """, (date, content_json, created_at))
        conn.commit()
    finally:
        release_db_connection(conn)


def get_digest(db_path, date):
    """Retrieves the digest for a specific date."""
    conn = get_db_connection()
    try:
        cursor = _dict_cursor(conn)
        cursor.execute("SELECT * FROM digests WHERE date = %s", (date,))
        row = cursor.fetchone()
        if row:
            row_dict = dict(row)
            row_dict["content"] = json.loads(row_dict["content"])
            return row_dict
        return None
    finally:
        release_db_connection(conn)


def get_latest_digest(db_path):
    """Retrieves the most recently generated digest."""
    conn = get_db_connection()
    try:
        cursor = _dict_cursor(conn)
        cursor.execute("SELECT * FROM digests ORDER BY date DESC LIMIT 1")
        row = cursor.fetchone()
        if row:
            row_dict = dict(row)
            row_dict["content"] = json.loads(row_dict["content"])
            return row_dict
        return None
    finally:
        release_db_connection(conn)


def get_all_digest_dates(db_path):
    """Returns a list of all dates for which a digest has been generated."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT date FROM digests ORDER BY date DESC")
        return [row[0] for row in cursor.fetchall()]
    finally:
        release_db_connection(conn)


def save_setting(db_path, key, value):
    """Saves a dynamic setting key-value pair."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO settings (key, value)
        VALUES (%s, %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """, (key, str(value)))
        conn.commit()
    finally:
        release_db_connection(conn)


def get_setting(db_path, key, default=None):
    """Retrieves a dynamic setting value by key."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = %s", (key,))
        row = cursor.fetchone()
        return row[0] if row else default
    finally:
        release_db_connection(conn)


# --- Search ---

def search_articles(db_path, query="", source_filter=None, limit=40):
    """Full-text search across raw article titles and descriptions."""
    conn = get_db_connection()
    try:
        cursor = _dict_cursor(conn)
        term = f"%{query}%"
        if source_filter:
            cursor.execute(
                """SELECT id, date, source, title, description, url, category
                   FROM raw_articles
                   WHERE (title ILIKE %s OR description ILIKE %s) AND source = %s
                   ORDER BY date DESC, id DESC LIMIT %s""",
                (term, term, source_filter, limit),
            )
        else:
            cursor.execute(
                """SELECT id, date, source, title, description, url, category
                   FROM raw_articles
                   WHERE title ILIKE %s OR description ILIKE %s
                   ORDER BY date DESC, id DESC LIMIT %s""",
                (term, term, limit),
            )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        release_db_connection(conn)


def get_distinct_sources(db_path):
    """Returns all distinct source names present in raw_articles."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT source FROM raw_articles ORDER BY source")
        return [row[0] for row in cursor.fetchall()]
    finally:
        release_db_connection(conn)


# --- Subscriber management ---

def add_subscriber(db_path, email, name=""):
    """Adds a new subscriber. Returns True if added, False if email already exists."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        subscribed_at = datetime.utcnow().isoformat()
        cursor.execute(
            """INSERT INTO subscribers (email, name, active, subscribed_at)
               VALUES (%s, %s, 1, %s)
               ON CONFLICT (email) DO NOTHING""",
            (email.strip().lower(), name.strip(), subscribed_at),
        )
        added = cursor.rowcount > 0
        conn.commit()
        return added
    finally:
        release_db_connection(conn)


def remove_subscriber(db_path, email):
    """Removes a subscriber by email. Returns True if a row was deleted."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM subscribers WHERE email = %s", (email.strip().lower(),))
        removed = cursor.rowcount > 0
        conn.commit()
        return removed
    finally:
        release_db_connection(conn)


def get_all_subscribers(db_path):
    """Returns all subscribers (active and inactive) as a list of dicts."""
    conn = get_db_connection()
    try:
        cursor = _dict_cursor(conn)
        cursor.execute(
            "SELECT id, email, name, active, subscribed_at FROM subscribers ORDER BY subscribed_at DESC"
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        release_db_connection(conn)


def get_active_subscribers(db_path):
    """Returns only active subscribers as a list of dicts with 'email' and 'name' keys."""
    conn = get_db_connection()
    try:
        cursor = _dict_cursor(conn)
        cursor.execute(
            "SELECT email, name FROM subscribers WHERE active = 1 ORDER BY subscribed_at"
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        release_db_connection(conn)


# --- Scraper configuration ---

DEFAULT_SCRAPER_CONFIG = {
    "hacker_news":  {"enabled": True,  "limit": 20},
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
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        created_at = datetime.utcnow().isoformat()
        cursor.execute("""
        INSERT INTO sources (name, url, source_type, enabled, created_at)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (url) DO NOTHING
        """, (name.strip(), url.strip(), source_type, enabled, created_at))
        added = cursor.rowcount > 0
        conn.commit()
        return added
    finally:
        release_db_connection(conn)


def get_sources(db_path):
    """Retrieves all dynamic news/blog sources."""
    conn = get_db_connection()
    try:
        cursor = _dict_cursor(conn)
        cursor.execute("SELECT * FROM sources ORDER BY id ASC")
        return [dict(row) for row in cursor.fetchall()]
    finally:
        release_db_connection(conn)


def get_source(db_path, source_id):
    """Retrieves a specific news/blog source by ID."""
    conn = get_db_connection()
    try:
        cursor = _dict_cursor(conn)
        cursor.execute("SELECT * FROM sources WHERE id = %s", (source_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        release_db_connection(conn)


def update_source(db_path, source_id, name, url, source_type, enabled):
    """Updates a dynamic news/blog source details."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        try:
            cursor.execute("""
            UPDATE sources
            SET name = %s, url = %s, source_type = %s, enabled = %s
            WHERE id = %s
            """, (name.strip(), url.strip(), source_type, int(enabled), source_id))
            conn.commit()
            return cursor.rowcount > 0
        except psycopg2.IntegrityError:
            conn.rollback()
            return False
    finally:
        release_db_connection(conn)


def delete_source(db_path, source_id):
    """Deletes a dynamic news/blog source."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sources WHERE id = %s", (source_id,))
        success = cursor.rowcount > 0
        conn.commit()
        return success
    finally:
        release_db_connection(conn)


def toggle_source(db_path, source_id):
    """Toggles the enabled status of a news/blog source. Returns (success, new_state)."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT enabled FROM sources WHERE id = %s", (source_id,))
        row = cursor.fetchone()
        if row:
            new_state = 0 if row[0] else 1
            cursor.execute("UPDATE sources SET enabled = %s WHERE id = %s", (new_state, source_id))
            conn.commit()
            return True, new_state
        return False, None
    finally:
        release_db_connection(conn)


# =============================================================================
# AUTH — Users & Sessions
# =============================================================================

def create_user(db_path, email, name="", password_hash="", avatar_url="", role="viewer"):
    """Creates a new user. Returns user dict on success, None if email already exists."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        created_at = datetime.utcnow().isoformat()
        try:
            cursor.execute("""
            INSERT INTO users (email, name, password_hash, avatar_url, created_at, role)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """, (email.strip().lower(), name.strip(), password_hash, avatar_url, created_at, role))
            user_id = cursor.fetchone()[0]
            conn.commit()
        except psycopg2.IntegrityError:
            conn.rollback()
            return None
    finally:
        release_db_connection(conn)
    return get_user_by_id(db_path, user_id)


def get_user_by_email(db_path, email):
    """Retrieves a user by email address."""
    conn = get_db_connection()
    try:
        cursor = _dict_cursor(conn)
        cursor.execute("SELECT * FROM users WHERE email = %s", (email.strip().lower(),))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        release_db_connection(conn)


def get_user_by_id(db_path, user_id):
    """Retrieves a user by ID."""
    conn = get_db_connection()
    try:
        cursor = _dict_cursor(conn)
        cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        release_db_connection(conn)


def update_user_last_login(db_path, user_id):
    """Stamps the user's last_login timestamp to now."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET last_login = %s WHERE id = %s",
                       (datetime.utcnow().isoformat(), user_id))
        conn.commit()
    finally:
        release_db_connection(conn)


def get_user_count(db_path):
    """Returns total number of registered users."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        row = cursor.fetchone()
        return row[0] if row else 0
    finally:
        release_db_connection(conn)


def get_all_users(db_path):
    """Returns all users as a list of dicts (password_hash excluded)."""
    conn = get_db_connection()
    try:
        cursor = _dict_cursor(conn)
        cursor.execute("""
            SELECT id, email, name, role, avatar_url, created_at, last_login
            FROM users ORDER BY created_at ASC
        """)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        release_db_connection(conn)


# ── Bookmark helpers ────────────────────────────────────────────────────────

def get_bookmarks(db_path, user_id):
    """Returns all bookmarks for a user, newest first."""
    conn = get_db_connection()
    try:
        cursor = _dict_cursor(conn)
        cursor.execute("""
            SELECT id, url, title, source, description, digest_date, bookmarked_at
            FROM bookmarks WHERE user_id = %s ORDER BY bookmarked_at DESC
        """, (user_id,))
        return [dict(row) for row in cursor.fetchall()]
    finally:
        release_db_connection(conn)


def add_bookmark(db_path, user_id, url, title, source="", description="", digest_date=""):
    """Adds a bookmark. Returns True if added, False if already bookmarked."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        bookmarked_at = datetime.utcnow().isoformat()
        cursor.execute("""
            INSERT INTO bookmarks (user_id, url, title, source, description, digest_date, bookmarked_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, url) DO NOTHING
        """, (user_id, url.strip(), title.strip(), source.strip(), description.strip(), digest_date.strip(), bookmarked_at))
        added = cursor.rowcount > 0
        conn.commit()
        return added
    finally:
        release_db_connection(conn)


def remove_bookmark(db_path, user_id, url):
    """Removes a bookmark. Returns True if removed."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM bookmarks WHERE user_id = %s AND url = %s", (user_id, url.strip()))
        removed = cursor.rowcount > 0
        conn.commit()
        return removed
    finally:
        release_db_connection(conn)


def is_bookmarked(db_path, user_id, url):
    """Returns True if the URL is already bookmarked by the user."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM bookmarks WHERE user_id = %s AND url = %s", (user_id, url.strip()))
        return cursor.fetchone() is not None
    finally:
        release_db_connection(conn)


def get_bookmark_urls(db_path, user_id):
    """Returns a set of bookmarked URLs for fast lookup."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT url FROM bookmarks WHERE user_id = %s", (user_id,))
        return {row[0] for row in cursor.fetchall()}
    finally:
        release_db_connection(conn)


# ── Weekly digest helpers ────────────────────────────────────────────────────

def get_digests_for_range(db_path, start_date_str, end_date_str):
    """Returns all digests between start_date and end_date inclusive, ordered by date DESC."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT date, content FROM digests
            WHERE date >= %s AND date <= %s
            ORDER BY date DESC
        """, (start_date_str, end_date_str))
        rows = cursor.fetchall()
        result = []
        for date, content in rows:
            try:
                result.append({"date": date, "content": json.loads(content)})
            except Exception:
                pass
        return result
    finally:
        release_db_connection(conn)


def update_user_role(db_path, user_id, role):
    """Sets a user's role ('admin' or 'viewer'). Returns True if a row was updated."""
    if role not in ("admin", "viewer"):
        return False
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET role = %s WHERE id = %s", (role, user_id))
        updated = cursor.rowcount > 0
        conn.commit()
        return updated
    finally:
        release_db_connection(conn)


def _hash_token(token):
    """Session tokens are high-entropy (secrets.token_urlsafe), so a fast hash is
    enough — this just means a DB leak doesn't hand out live sessions directly."""
    import hashlib
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def save_session(db_path, token, user_id, expires_hours=720):
    """Stores a new session token (hashed). Default expiry: 30 days (720 hours)."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        created_at = datetime.utcnow().isoformat()
        expires_at = (datetime.utcnow() + timedelta(hours=expires_hours)).isoformat()
        cursor.execute("""
        INSERT INTO sessions (token, user_id, created_at, expires_at)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (token) DO UPDATE SET
            user_id = EXCLUDED.user_id,
            created_at = EXCLUDED.created_at,
            expires_at = EXCLUDED.expires_at
        """, (_hash_token(token), user_id, created_at, expires_at))
        conn.commit()
    finally:
        release_db_connection(conn)


def get_session(db_path, token):
    """Retrieves a session by token. Returns None if expired or not found."""
    conn = get_db_connection()
    try:
        cursor = _dict_cursor(conn)
        cursor.execute("SELECT * FROM sessions WHERE token = %s", (_hash_token(token),))
        row = cursor.fetchone()
        if not row:
            return None
        session = dict(row)
    finally:
        release_db_connection(conn)

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
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sessions WHERE token = %s", (_hash_token(token),))
        conn.commit()
    finally:
        release_db_connection(conn)


def delete_expired_sessions(db_path):
    """Cleans up all expired sessions."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        now = datetime.utcnow().isoformat()
        cursor.execute("DELETE FROM sessions WHERE expires_at < %s", (now,))
        conn.commit()
    finally:
        release_db_connection(conn)


def get_previous_digest_date(db_path, before_date):
    """Finds the latest compiled digest date strictly before before_date."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT date FROM digests WHERE date < %s AND content != '' AND content != '{}' ORDER BY date DESC LIMIT 1",
            (before_date,)
        )
        row = cursor.fetchone()
        return row[0] if row else None
    finally:
        release_db_connection(conn)


def get_raw_articles_stream(db_path, limit=15, offset=0, source_filter=None):
    """Returns a paginated list of raw articles for the infinite raw stream."""
    conn = get_db_connection()
    try:
        cursor = _dict_cursor(conn)
        if source_filter and source_filter.strip() and source_filter.lower() != "all":
            sf = f"%{source_filter.strip().lower()}%"
            cursor.execute(
                "SELECT id, date, source, title, description, url, category, fetched_at FROM raw_articles WHERE LOWER(source) LIKE %s ORDER BY id DESC LIMIT %s OFFSET %s",
                (sf, limit, offset)
            )
        else:
            cursor.execute(
                "SELECT id, date, source, title, description, url, category, fetched_at FROM raw_articles ORDER BY id DESC LIMIT %s OFFSET %s",
                (limit, offset)
            )
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        release_db_connection(conn)


def get_raw_articles_by_date(db_path, date_str):
    """Retrieves all raw articles for a given date string or matching fetched_at date prefix."""
    conn = get_db_connection()
    try:
        cursor = _dict_cursor(conn)
        cursor.execute(
            "SELECT * FROM raw_articles WHERE date = %s OR fetched_at LIKE %s ORDER BY id DESC",
            (date_str, f"{date_str}%")
        )
        return [dict(r) for r in cursor.fetchall()]
    finally:
        release_db_connection(conn)


