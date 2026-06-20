"""
Database operations for TG Stream Server.
Handles content groups, sources, users, ads, and legacy video storage.
Uses the SAME MySQL database as the PHP website.
"""
import secrets
import pymysql
import logging
from contextlib import contextmanager
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# CONNECTION
# ══════════════════════════════════════════════════════════════

@contextmanager
def get_connection():
    """Get a MySQL connection with auto-close."""
    conn = pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER,
        password=DB_PASS, database=DB_NAME,
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
        autocommit=True, connect_timeout=10, read_timeout=30,
    )
    try:
        yield conn
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════
# TABLE CREATION
# ══════════════════════════════════════════════════════════════

def ensure_tables():
    """Create all required tables if they don't exist."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            # ── Content groups (1 row = 1 movie/episode) ─────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tg_content (
                    id          INT AUTO_INCREMENT PRIMARY KEY,
                    title       VARCHAR(255) NOT NULL,
                    slug        VARCHAR(255) NOT NULL UNIQUE,
                    description TEXT DEFAULT '',
                    thumbnail   VARCHAR(512) DEFAULT '',
                    owner_id    BIGINT DEFAULT 0,
                    is_active   TINYINT(1) DEFAULT 1,
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_slug (slug),
                    INDEX idx_active (is_active)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

            # ── Sources (1 row = 1 file_id = 1 lang+quality) ─
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tg_sources (
                    id          INT AUTO_INCREMENT PRIMARY KEY,
                    content_id  INT NOT NULL,
                    file_id     VARCHAR(512) NOT NULL,
                    file_unique_id VARCHAR(128) DEFAULT '',
                    file_size   BIGINT DEFAULT 0,
                    duration    INT DEFAULT 0,
                    width       INT DEFAULT 0,
                    height      INT DEFAULT 0,
                    language    VARCHAR(50) NOT NULL DEFAULT 'Hindi',
                    quality     VARCHAR(20) NOT NULL DEFAULT '720p',
                    label       VARCHAR(100) DEFAULT '',
                    sort_order  INT DEFAULT 0,
                    is_active   TINYINT(1) DEFAULT 1,
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_content (content_id),
                    UNIQUE KEY uq_source (content_id, language, quality)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

            # ── API users (embed access control) ─────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tg_api_users (
                    id          INT AUTO_INCREMENT PRIMARY KEY,
                    name        VARCHAR(100) NOT NULL,
                    api_key     VARCHAR(64) NOT NULL UNIQUE,
                    plan        VARCHAR(20) DEFAULT 'free',
                    max_views   INT DEFAULT 1000,
                    max_embeds  INT DEFAULT 10,
                    is_active   TINYINT(1) DEFAULT 1,
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_key (api_key)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

            # ── Ads configuration ────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tg_ads (
                    id          INT AUTO_INCREMENT PRIMARY KEY,
                    name        VARCHAR(100) NOT NULL,
                    ad_type     VARCHAR(20) DEFAULT 'custom',
                    ad_url      VARCHAR(512) DEFAULT '',
                    ad_html     TEXT DEFAULT '',
                    position    VARCHAR(10) DEFAULT 'pre',
                    duration    INT DEFAULT 5,
                    is_active   TINYINT(1) DEFAULT 1,
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

            # ── Legacy: individual videos (from bot detection) ─
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tg_videos (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    file_id VARCHAR(512) NOT NULL,
                    file_unique_id VARCHAR(128) NOT NULL,
                    file_size BIGINT DEFAULT 0,
                    duration INT DEFAULT 0,
                    width INT DEFAULT 0,
                    height INT DEFAULT 0,
                    file_name VARCHAR(512) DEFAULT '',
                    mime_type VARCHAR(64) DEFAULT 'video/mp4',
                    caption TEXT,
                    message_id INT DEFAULT 0,
                    channel_id BIGINT DEFAULT 0,
                    quality VARCHAR(20) DEFAULT '720p',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_file_id (file_id(100)),
                    INDEX idx_unique_id (file_unique_id(100))
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

            # ── View logs ────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tg_view_logs (
                    id          INT AUTO_INCREMENT PRIMARY KEY,
                    content_id  INT DEFAULT NULL,
                    source_id   INT DEFAULT NULL,
                    ip_hash     VARCHAR(64) DEFAULT '',
                    user_agent  VARCHAR(255) DEFAULT '',
                    viewed_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_content (content_id),
                    INDEX idx_date (viewed_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

    logger.info("All database tables verified.")


# ══════════════════════════════════════════════════════════════
# CONTENT CRUD
# ══════════════════════════════════════════════════════════════

def create_content(title: str, slug: str, owner_id: int = 0,
                   description: str = "", thumbnail: str = "") -> int:
    """Create a new content group. Returns the new content ID."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tg_content (title, slug, description, thumbnail, owner_id)
                VALUES (%s, %s, %s, %s, %s)
            """, (title, slug, description, thumbnail, owner_id))
            return cur.lastrowid


def get_content_by_slug(slug: str) -> dict | None:
    """Get content group by slug."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM tg_content WHERE slug = %s AND is_active = 1 LIMIT 1", (slug,))
            return cur.fetchone()


def get_content_by_id(content_id: int) -> dict | None:
    """Get content group by ID."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM tg_content WHERE id = %s LIMIT 1", (content_id,))
            return cur.fetchone()


def list_content(limit: int = 100) -> list:
    """List all content groups."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.*, COUNT(s.id) AS source_count
                FROM tg_content c
                LEFT JOIN tg_sources s ON s.content_id = c.id AND s.is_active = 1
                GROUP BY c.id
                ORDER BY c.created_at DESC
                LIMIT %s
            """, (limit,))
            return cur.fetchall()


def delete_content(content_id: int):
    """Delete a content group and its sources."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM tg_sources WHERE content_id = %s", (content_id,))
            cur.execute("DELETE FROM tg_content WHERE id = %s", (content_id,))


def update_content(content_id: int, **kwargs):
    """Update content fields."""
    allowed = {"title", "slug", "description", "thumbnail", "is_active"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = %s" for k in fields)
    values = list(fields.values()) + [content_id]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE tg_content SET {set_clause} WHERE id = %s", values)


# ══════════════════════════════════════════════════════════════
# SOURCE CRUD
# ══════════════════════════════════════════════════════════════

def add_source(content_id: int, file_id: str, language: str, quality: str,
               file_unique_id: str = "", file_size: int = 0, duration: int = 0,
               width: int = 0, height: int = 0, label: str = "") -> int:
    """Add a video source to a content group. Returns source ID."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tg_sources
                    (content_id, file_id, file_unique_id, file_size, duration,
                     width, height, language, quality, label)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    file_id = VALUES(file_id),
                    file_size = VALUES(file_size),
                    duration = VALUES(duration),
                    width = VALUES(width),
                    height = VALUES(height)
            """, (content_id, file_id, file_unique_id, file_size, duration,
                  width, height, language, quality, label))
            return cur.lastrowid


def get_sources_by_content(content_id: int) -> list:
    """Get all active sources for a content group."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM tg_sources
                WHERE content_id = %s AND is_active = 1
                ORDER BY language, FIELD(quality, '2160p', '1080p', '720p', '480p', '360p'), sort_order
            """, (content_id,))
            return cur.fetchall()


def get_source_by_id(source_id: int) -> dict | None:
    """Get a single source by ID."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM tg_sources WHERE id = %s LIMIT 1", (source_id,))
            return cur.fetchone()


def delete_source(source_id: int):
    """Delete a source."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM tg_sources WHERE id = %s", (source_id,))


# ══════════════════════════════════════════════════════════════
# API USERS CRUD
# ══════════════════════════════════════════════════════════════

def create_api_user(name: str, plan: str = "free",
                    max_views: int = 1000, max_embeds: int = 10) -> dict:
    """Create a new API user. Returns user dict with api_key."""
    api_key = secrets.token_hex(32)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tg_api_users (name, api_key, plan, max_views, max_embeds)
                VALUES (%s, %s, %s, %s, %s)
            """, (name, api_key, plan, max_views, max_embeds))
            return {"id": cur.lastrowid, "name": name, "api_key": api_key, "plan": plan}


def list_api_users() -> list:
    """List all API users."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM tg_api_users ORDER BY created_at DESC")
            return cur.fetchall()


def delete_api_user(user_id: int):
    """Delete an API user."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM tg_api_users WHERE id = %s", (user_id,))


def update_api_user(user_id: int, **kwargs):
    """Update API user fields."""
    allowed = {"name", "plan", "max_views", "max_embeds", "is_active"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = %s" for k in fields)
    values = list(fields.values()) + [user_id]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE tg_api_users SET {set_clause} WHERE id = %s", values)


# ══════════════════════════════════════════════════════════════
# ADS CRUD
# ══════════════════════════════════════════════════════════════

def get_active_ads(position: str = None) -> list:
    """Get active ads, optionally filtered by position."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            if position:
                cur.execute(
                    "SELECT * FROM tg_ads WHERE is_active = 1 AND position = %s ORDER BY id",
                    (position,))
            else:
                cur.execute("SELECT * FROM tg_ads WHERE is_active = 1 ORDER BY position, id")
            return cur.fetchall()


def create_ad(name: str, ad_type: str = "custom", ad_url: str = "",
              ad_html: str = "", position: str = "pre", duration: int = 5) -> int:
    """Create a new ad. Returns ad ID."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tg_ads (name, ad_type, ad_url, ad_html, position, duration)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (name, ad_type, ad_url, ad_html, position, duration))
            return cur.lastrowid


def delete_ad(ad_id: int):
    """Delete an ad."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM tg_ads WHERE id = %s", (ad_id,))


def list_ads() -> list:
    """List all ads."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM tg_ads ORDER BY position, id")
            return cur.fetchall()


def toggle_ad(ad_id: int, active: bool):
    """Toggle ad active state."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE tg_ads SET is_active = %s WHERE id = %s", (1 if active else 0, ad_id))


# ══════════════════════════════════════════════════════════════
# VIEW LOGS
# ══════════════════════════════════════════════════════════════

def log_view(content_id: int = None, source_id: int = None,
             ip_hash: str = "", user_agent: str = ""):
    """Log a view."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tg_view_logs (content_id, source_id, ip_hash, user_agent)
                VALUES (%s, %s, %s, %s)
            """, (content_id, source_id, ip_hash, user_agent[:255]))


def get_view_stats() -> dict:
    """Get aggregate view stats."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS total FROM tg_view_logs")
            total = cur.fetchone()["total"]
            cur.execute("SELECT COUNT(*) AS today FROM tg_view_logs WHERE DATE(viewed_at) = CURDATE()")
            today = cur.fetchone()["today"]
            cur.execute("SELECT COUNT(DISTINCT ip_hash) AS unique_ips FROM tg_view_logs")
            unique = cur.fetchone()["unique_ips"]
            return {"total": total, "today": today, "unique_ips": unique}


def get_recent_logs(limit: int = 50) -> list:
    """Get recent view logs."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT l.*, c.title AS content_title, c.slug
                FROM tg_view_logs l
                LEFT JOIN tg_content c ON c.id = l.content_id
                ORDER BY l.viewed_at DESC LIMIT %s
            """, (limit,))
            return cur.fetchall()


# ══════════════════════════════════════════════════════════════
# LEGACY — Individual videos (backward compat)
# ══════════════════════════════════════════════════════════════

def save_video(data: dict) -> int:
    """Save or update video metadata from bot detection."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tg_videos
                    (file_id, file_unique_id, file_size, duration, width, height,
                     file_name, mime_type, caption, message_id, channel_id, quality)
                VALUES
                    (%(file_id)s, %(file_unique_id)s, %(file_size)s, %(duration)s,
                     %(width)s, %(height)s, %(file_name)s, %(mime_type)s,
                     %(caption)s, %(message_id)s, %(channel_id)s, %(quality)s)
                ON DUPLICATE KEY UPDATE
                    file_id = VALUES(file_id), file_size = VALUES(file_size),
                    duration = VALUES(duration), width = VALUES(width),
                    height = VALUES(height), file_name = VALUES(file_name),
                    caption = VALUES(caption)
            """, data)
            return cur.lastrowid


def get_video_by_file_id(file_id: str) -> dict | None:
    """Look up video metadata by file_id."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM tg_videos WHERE file_id = %s LIMIT 1", (file_id,))
            row = cur.fetchone()
            if row:
                return row
            cur.execute(
                "SELECT * FROM tg_sources WHERE file_id = %s LIMIT 1", (file_id,))
            row = cur.fetchone()
            if row:
                return row
            cur.execute(
                "SELECT telegram_file_id AS file_id, file_size_mb, duration_seconds AS duration "
                "FROM streaming_sources WHERE telegram_file_id = %s LIMIT 1", (file_id,))
            row = cur.fetchone()
            if row:
                row["file_size"] = int(float(row.get("file_size_mb", 0)) * 1024 * 1024)
                return row
    return None


def get_all_videos(limit: int = 100) -> list:
    """Get all saved videos."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM tg_videos ORDER BY created_at DESC LIMIT %s", (limit,))
            return cur.fetchall()
