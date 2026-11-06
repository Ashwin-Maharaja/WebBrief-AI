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
                    created_at_formatted TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_history_created_at ON summary_history(created_at DESC)")
        conn.close()
    except Exception as e:
        logger.warning("Failed to initialize SQLite history database: %s", str(e))


def add_history_entry(
    title: str,
    url: str,
    mode: str,
    mode_name: str,
    summary: Dict[str, Any],
    db_path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Adds a new summary entry to the history database.

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

    try:
        conn = get_db_connection(db_path)
        with conn:
            conn.execute(
                """
                INSERT INTO summary_history (
                    id, title, url, mode, mode_name, summary_json, created_at, created_at_formatted
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
            SELECT id, title, url, mode, mode_name, summary_json, created_at, created_at_formatted
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

            entries.append({
                "id": row["id"],
                "title": row["title"],
                "url": row["url"],
                "mode": row["mode"],
                "mode_name": row["mode_name"],
                "summary": parsed_summary,
                "created_at_formatted": row["created_at_formatted"],
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
            SELECT id, title, url, mode, mode_name, summary_json, created_at, created_at_formatted
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

            return {
                "id": row["id"],
                "title": row["title"],
                "url": row["url"],
                "mode": row["mode"],
                "mode_name": row["mode_name"],
                "summary": parsed_summary,
                "created_at_formatted": row["created_at_formatted"],
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
