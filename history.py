import datetime
import json
import logging
import os
import sqlite3
import uuid
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)

DEFAULT_DB_FILENAME = "webbrief_history.db"


def get_default_db_path() -> str:
    """Returns the default SQLite database path in the application directory."""
    workspace_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(workspace_dir, DEFAULT_DB_FILENAME)


def get_db_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """
    Creates and returns a SQLite connection with row factory.
    Gracefully falls back to an in-memory database if disk is read-only or inaccessible.
    """
    target_path = db_path or get_default_db_path()
    try:
        conn = sqlite3.connect(target_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn
    except (sqlite3.OperationalError, OSError):
        # Fallback to in-memory SQLite if running in read-only/ephemeral serverless environment
        conn = sqlite3.connect(":memory:", timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn


def init_db(db_path: Optional[str] = None) -> None:
    """Initializes the database schema if not already present."""
    try:
        conn = get_db_connection(db_path)
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS summary_history (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    mode_name TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    created_at_formatted TEXT NOT NULL,
                    entry_type TEXT DEFAULT 'single',
                    url_b TEXT DEFAULT NULL,
                    title_b TEXT DEFAULT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_history_created_at ON summary_history(created_at DESC)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS preferences (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)

            # Auto-migrate v1.3 columns if existing database created on older version
            for col_def in [
                ("entry_type", "TEXT DEFAULT 'single'"),
                ("url_b", "TEXT DEFAULT NULL"),
                ("title_b", "TEXT DEFAULT NULL"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE summary_history ADD COLUMN {col_def[0]} {col_def[1]}")
                except sqlite3.OperationalError:
                    pass
        conn.close()
    except Exception as e:
        logger.warning("Failed to initialize SQLite history database: %s", str(e))


def add_history_entry(
    title: str,
    url: str,
    mode: str,
    mode_name: str,
    summary: Dict[str, Any],
    entry_type: str = "single",
    url_b: Optional[str] = None,
    title_b: Optional[str] = None,
    db_path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Adds a new summary or comparison entry to the history database.

    Returns:
        The newly created history entry dictionary, or None on failure.
    """
    init_db(db_path)
    entry_id = str(uuid.uuid4())
    now = datetime.datetime.now()
    created_at_formatted = now.strftime("%b %d, %Y • %I:%M %p")

    # Serialize summary dictionary safely
    summary_json = json.dumps(summary)
    safe_title = (title or "Untitled Webpage").strip()
    safe_url = (url or "").strip()
    safe_url_b = (url_b or "").strip() if url_b else None
    safe_title_b = (title_b or "").strip() if title_b else None

    try:
        conn = get_db_connection(db_path)
        with conn:
            conn.execute(
                """
                INSERT INTO summary_history (
                    id, title, url, mode, mode_name, summary_json, created_at, created_at_formatted,
                    entry_type, url_b, title_b
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    safe_title,
                    safe_url,
                    mode,
                    mode_name,
                    summary_json,
                    now.isoformat(),
                    created_at_formatted,
                    entry_type,
                    safe_url_b,
                    safe_title_b,
                )
            )
        conn.close()

        return {
            "id": entry_id,
            "title": safe_title,
            "url": safe_url,
            "mode": mode,
            "mode_name": mode_name,
            "summary": summary,
            "created_at_formatted": created_at_formatted,
            "entry_type": entry_type,
            "url_b": safe_url_b,
            "title_b": safe_title_b,
        }
    except Exception as e:
        logger.warning("Failed to add history entry: %s", str(e))
        return None


def get_history(limit: int = 50, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Retrieves the most recent summary history entries.
    """
    init_db(db_path)
    entries = []
    try:
        conn = get_db_connection(db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, title, url, mode, mode_name, summary_json, created_at, created_at_formatted,
                   entry_type, url_b, title_b
            FROM summary_history
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,)
        )
        rows = cursor.fetchall()
        for row in rows:
            try:
                parsed_summary = json.loads(row["summary_json"])
            except Exception:
                parsed_summary = {}

            entry_type = "single"
            url_b = None
            title_b = None
            try:
                entry_type = row["entry_type"] or "single"
                url_b = row["url_b"]
                title_b = row["title_b"]
            except (IndexError, KeyError):
                pass

            entries.append({
                "id": row["id"],
                "title": row["title"],
                "url": row["url"],
                "mode": row["mode"],
                "mode_name": row["mode_name"],
                "summary": parsed_summary,
                "created_at_formatted": row["created_at_formatted"],
                "entry_type": entry_type,
                "url_b": url_b,
                "title_b": title_b,
            })
        conn.close()
    except Exception as e:
        logger.warning("Failed to retrieve summary history: %s", str(e))

    return entries


def get_history_entry(entry_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Retrieves a single summary history entry by its ID.
    """
    init_db(db_path)
    try:
        conn = get_db_connection(db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, title, url, mode, mode_name, summary_json, created_at, created_at_formatted,
                   entry_type, url_b, title_b
            FROM summary_history
            WHERE id = ?
            """,
            (entry_id,)
        )
        row = cursor.fetchone()
        conn.close()

        if row:
            try:
                parsed_summary = json.loads(row["summary_json"])
            except Exception:
                parsed_summary = {}

            entry_type = "single"
            url_b = None
            title_b = None
            try:
                entry_type = row["entry_type"] or "single"
                url_b = row["url_b"]
                title_b = row["title_b"]
            except (IndexError, KeyError):
                pass

            return {
                "id": row["id"],
                "title": row["title"],
                "url": row["url"],
                "mode": row["mode"],
                "mode_name": row["mode_name"],
                "summary": parsed_summary,
                "created_at_formatted": row["created_at_formatted"],
                "entry_type": entry_type,
                "url_b": url_b,
                "title_b": title_b,
            }
    except Exception as e:
        logger.warning("Failed to fetch history entry %s: %s", entry_id, str(e))

    return None


def delete_history_entry(entry_id: str, db_path: Optional[str] = None) -> bool:
    """
    Deletes an individual history entry by its ID.
    """
    init_db(db_path)
    try:
        conn = get_db_connection(db_path)
        with conn:
            cursor = conn.execute("DELETE FROM summary_history WHERE id = ?", (entry_id,))
            affected = cursor.rowcount
        conn.close()
        return affected > 0
    except Exception as e:
        logger.warning("Failed to delete history entry %s: %s", entry_id, str(e))
        return False


def clear_history(db_path: Optional[str] = None) -> bool:
    """
    Clears all entries from the summary history database.
    """
    init_db(db_path)
    try:
        conn = get_db_connection(db_path)
        with conn:
            conn.execute("DELETE FROM summary_history")
        conn.close()
        return True
    except Exception as e:
        logger.warning("Failed to clear summary history: %s", str(e))
        return False


# -------------------------------------------------------------
# Preferences & Custom Prompt Directives Storage
# -------------------------------------------------------------
MAX_DIRECTIVE_LENGTH = 1000

# In-memory preferences fallback for serverless/read-only environments
_FALLBACK_PREFS: Dict[str, str] = {
    "custom_directive": "",
    "custom_directive_enabled": "0"
}


def get_preference(key: str, default: Optional[str] = None, db_path: Optional[str] = None) -> Optional[str]:
    """Retrieves a single preference value by key."""
    init_db(db_path)
    try:
        conn = get_db_connection(db_path)
        cursor = conn.execute("SELECT value FROM preferences WHERE key = ?", (key,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return row["value"]
    except Exception as e:
        logger.warning("Failed to read preference %s: %s", key, str(e))
    return _FALLBACK_PREFS.get(key, default)


def set_preference(key: str, value: str, db_path: Optional[str] = None) -> bool:
    """Sets a single preference value."""
    init_db(db_path)
    _FALLBACK_PREFS[key] = str(value)
    try:
        conn = get_db_connection(db_path)
        with conn:
            conn.execute("""
                INSERT INTO preferences (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, (key, str(value)))
        conn.close()
        return True
    except Exception as e:
        logger.warning("Failed to write preference %s: %s", key, str(e))
        return False


def get_custom_directives(db_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Retrieves the custom prompt directive and its enabled state.
    Returns:
        dict: {'custom_directive': str, 'custom_directive_enabled': bool}
    """
    directive = get_preference("custom_directive", default="", db_path=db_path) or ""
    enabled_val = get_preference("custom_directive_enabled", default="0", db_path=db_path)
    return {
        "custom_directive": directive.strip(),
        "custom_directive_enabled": enabled_val in ("1", "true", "True", True)
    }


def save_custom_directives(directive: str, enabled: bool = True, db_path: Optional[str] = None) -> tuple[bool, str]:
    """
    Validates, sanitizes, and persists custom prompt directives.
    Enforces a strict MAX_DIRECTIVE_LENGTH limit.
    """
    if directive is None:
        directive = ""

    cleaned = str(directive).strip()
    if len(cleaned) > MAX_DIRECTIVE_LENGTH:
        return False, f"Custom instructions exceed maximum limit of {MAX_DIRECTIVE_LENGTH} characters."

    set_preference("custom_directive", cleaned, db_path=db_path)
    set_preference("custom_directive_enabled", "1" if enabled else "0", db_path=db_path)
    return True, "Custom instructions saved successfully."


def reset_custom_directives(db_path: Optional[str] = None) -> bool:
    """
    Resets custom prompt directives to blank and disabled.
    """
    set_preference("custom_directive", "", db_path=db_path)
    set_preference("custom_directive_enabled", "0", db_path=db_path)
    return True

