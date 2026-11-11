import datetime
import json
import logging
import os
import sqlite3
import uuid
from typing import List, Optional, Dict, Any

from byok import is_serverless_env

logger = logging.getLogger(__name__)

DEFAULT_DB_FILENAME = "webbrief_history.db"


class HistoryError(Exception):
    """Base exception for all history persistence and retrieval errors."""
    pass


class HistoryStorageUnavailableError(HistoryError):
    """Raised when the SQLite database or storage medium is unavailable or unreachable."""
    pass


class HistorySchemaError(HistoryError):
    """Raised when database schema is corrupted, missing, or mismatched."""
    pass


class HistoryTransactionError(HistoryError):
    """Raised when a history transaction or statement execution fails."""
    pass


def get_default_db_path() -> str:
    """Returns the default SQLite database path in the application directory."""
    workspace_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(workspace_dir, DEFAULT_DB_FILENAME)


def get_db_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """
    Creates and returns a SQLite connection with row factory.
    Enforces foreign keys on every connection (PRAGMA foreign_keys = ON).
    Enables WAL mode and busy timeout on disk-backed databases.
    Does NOT silently fall back to an in-memory database on disk connection failures.
    """
    if is_serverless_env() and db_path is None:
        raise HistoryStorageUnavailableError("SQLite database is disabled in serverless hosting environment.")

    target_path = db_path or get_default_db_path()
    try:
        conn = sqlite3.connect(target_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        # Enforce foreign keys and busy timeout on all connections (HIS-04, P2-HIS-01, P2-HIS-03)
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 10000;")
        if target_path != ":memory:":
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
            except sqlite3.OperationalError:
                pass
        return conn
    except (sqlite3.OperationalError, OSError) as e:
        logger.error("Failed to connect to SQLite database at '%s': %s", target_path, str(e))
        raise HistoryStorageUnavailableError(f"Failed to connect to SQLite database at '{target_path}': {str(e)}") from e


_INITIALIZED_DBS: set = set()


def init_db(db_path: Optional[str] = None) -> None:
    """Initializes the database schema if not already present."""
    if is_serverless_env() and db_path is None:
        return

    target_path = db_path or get_default_db_path()
    if target_path in _INITIALIZED_DBS:
        return

    try:
        conn = get_db_connection(db_path)
    except HistoryStorageUnavailableError:
        _INITIALIZED_DBS.discard(target_path)
        raise

    try:
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

            # Auto-migrate comparison columns if existing database created on older schema
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
        _INITIALIZED_DBS.add(target_path)

        # Initialize vector store table in the same database
        try:
            from vector_store import init_vector_store
            init_vector_store(db_path)
        except Exception as e:
            logger.debug("Vector store table init deferred: %s", str(e))

    except Exception as e:
        logger.error("Failed to initialize SQLite history database at '%s': %s", target_path, str(e))
        _INITIALIZED_DBS.discard(target_path)
        raise HistorySchemaError(f"Failed to initialize SQLite history database at '{target_path}': {str(e)}") from e


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
) -> Dict[str, Any]:
    """
    Adds a new summary or comparison entry to the history database.

    Returns:
        The newly created history entry dictionary when successfully saved.
    Raises:
        HistoryError: When the database insert or transaction fails.
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

    conn = get_db_connection(db_path)
    try:
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

        is_partial = bool(summary.get("is_partial") or summary.get("partial")) if isinstance(summary, dict) else False
        warning = summary.get("warning") if isinstance(summary, dict) else None

        return {
            "id": entry_id,
            "title": safe_title,
            "url": safe_url,
            "mode": mode,
            "mode_name": mode_name,
            "summary": summary,
            "created_at": now.isoformat(),
            "created_at_formatted": created_at_formatted,
            "entry_type": entry_type,
            "url_b": safe_url_b,
            "title_b": safe_title_b,
            "is_partial": is_partial,
            "warning": warning,
        }
    except Exception as e:
        logger.error("Failed to add SQLite history entry: %s", str(e))
        try:
            conn.close()
        except Exception:
            pass
        raise HistoryTransactionError(f"Failed to persist history entry: {str(e)}") from e


def get_history(limit: int = 50, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Retrieves the most recent summary history entries.
    Pass limit=-1 or limit<=0 to retrieve all entries without truncation.
    """
    if is_serverless_env() and db_path is None:
        return []

    init_db(db_path)
    entries = []
    effective_limit = -1 if (limit is None or limit <= 0) else limit
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, title, url, mode, mode_name, summary_json, created_at, created_at_formatted,
                   entry_type, url_b, title_b
            FROM summary_history
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (effective_limit,)
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

            is_partial = bool(parsed_summary.get("is_partial") or parsed_summary.get("partial")) if isinstance(parsed_summary, dict) else False
            warning = parsed_summary.get("warning") if isinstance(parsed_summary, dict) else None

            entries.append({
                "id": row["id"],
                "title": row["title"],
                "url": row["url"],
                "mode": row["mode"],
                "mode_name": row["mode_name"],
                "summary": parsed_summary,
                "created_at": row["created_at"],
                "created_at_formatted": row["created_at_formatted"],
                "entry_type": entry_type,
                "url_b": url_b,
                "title_b": title_b,
                "is_partial": is_partial,
                "warning": warning,
            })
        conn.close()
        return entries
    except Exception as e:
        logger.error("Failed to retrieve summary history: %s", str(e))
        try:
            conn.close()
        except Exception:
            pass
        raise HistoryTransactionError(f"Failed to retrieve summary history: {str(e)}") from e


def get_history_count(db_path: Optional[str] = None) -> int:
    """
    Returns the total count of saved summary records in SQLite history.
    """
    if is_serverless_env() and db_path is None:
        return 0

    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS cnt FROM summary_history")
        row = cur.fetchone()
        count = int(row["cnt"]) if row else 0
        conn.close()
        return count
    except Exception as e:
        logger.error("Failed to count summary history: %s", str(e))
        try:
            conn.close()
        except Exception:
            pass
        raise HistoryTransactionError(f"Failed to count summary history: {str(e)}") from e


def get_history_entry(entry_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Retrieves a single summary history entry by its ID.
    Returns None if entry does not exist.
    Raises HistoryError on database failure.
    """
    if is_serverless_env() and db_path is None:
        return None

    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
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

            is_partial = bool(parsed_summary.get("is_partial") or parsed_summary.get("partial")) if isinstance(parsed_summary, dict) else False
            warning = parsed_summary.get("warning") if isinstance(parsed_summary, dict) else None

            return {
                "id": row["id"],
                "title": row["title"],
                "url": row["url"],
                "mode": row["mode"],
                "mode_name": row["mode_name"],
                "summary": parsed_summary,
                "created_at": row["created_at"],
                "created_at_formatted": row["created_at_formatted"],
                "entry_type": entry_type,
                "url_b": url_b,
                "title_b": title_b,
                "is_partial": is_partial,
                "warning": warning,
            }
        return None
    except Exception as e:
        logger.error("Failed to fetch history entry %s: %s", entry_id, str(e))
        try:
            conn.close()
        except Exception:
            pass
        raise HistoryTransactionError(f"Failed to fetch history entry: {str(e)}") from e


def delete_history_entry(entry_id: str, db_path: Optional[str] = None) -> bool:
    """
    Deletes an individual history entry by its ID in a single atomic transaction.
    Foreign keys ensure vector embeddings are cascade-deleted.
    """
    if is_serverless_env() and db_path is None:
        return True

    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        with conn:
            # Explicit delete of embeddings in same transaction for defense-in-depth (HIS-05)
            try:
                conn.execute("DELETE FROM summary_embeddings WHERE summary_id = ?", (entry_id,))
            except sqlite3.OperationalError:
                pass
            cursor = conn.execute("DELETE FROM summary_history WHERE id = ?", (entry_id,))
            affected = cursor.rowcount
        conn.close()
        return affected > 0
    except Exception as e:
        logger.error("Failed to delete history entry %s: %s", entry_id, str(e))
        try:
            conn.close()
        except Exception:
            pass
        raise HistoryTransactionError(f"Failed to delete history entry: {str(e)}") from e


def clear_history(db_path: Optional[str] = None) -> bool:
    """
    Clears all entries from the summary history database and vector embeddings in a single atomic transaction.
    """
    if is_serverless_env() and db_path is None:
        return True

    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        with conn:
            try:
                conn.execute("DELETE FROM summary_embeddings")
            except sqlite3.OperationalError:
                pass
            conn.execute("DELETE FROM summary_history")
        conn.close()
        return True
    except Exception as e:
        logger.error("Failed to clear summary history: %s", str(e))
        try:
            conn.close()
        except Exception:
            pass
        raise HistoryTransactionError(f"Failed to clear history: {str(e)}") from e


def backup_database(destination_path: str, db_path: Optional[str] = None) -> bool:
    """
    Safely creates an online, consistent backup of the SQLite database (DB-01).
    Accounts for WAL mode by flushing WAL frames or using SQLite's online backup API,
    guaranteeing consistency without copying inconsistent live file states.
    """
    if is_serverless_env() and db_path is None:
        raise HistoryStorageUnavailableError("SQLite database backup is disabled in serverless hosting environment.")

    init_db(db_path)
    dest_dir = os.path.dirname(os.path.abspath(destination_path))
    if dest_dir and not os.path.exists(dest_dir):
        os.makedirs(dest_dir, exist_ok=True)

    src_conn = None
    dest_conn = None
    try:
        src_conn = get_db_connection(db_path)
        try:
            # Checkpoint WAL first to flush committed frames
            src_conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            dest_escaped = destination_path.replace("'", "''")
            src_conn.execute(f"VACUUM INTO '{dest_escaped}';")
            src_conn.close()
            return True
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            dest_conn = sqlite3.connect(destination_path)
            with dest_conn:
                src_conn.backup(dest_conn, pages=100)
            dest_conn.close()
            src_conn.close()
            return True
    except Exception as e:
        logger.error("Failed to backup SQLite database to '%s': %s", destination_path, str(e))
        if src_conn:
            try:
                src_conn.close()
            except Exception:
                pass
        if dest_conn:
            try:
                dest_conn.close()
            except Exception:
                pass
        raise HistoryTransactionError(f"Database backup failed: {str(e)}") from e


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
    if is_serverless_env() and db_path is None:
        return _FALLBACK_PREFS.get(key, default)
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
    _FALLBACK_PREFS[key] = str(value)
    if is_serverless_env() and db_path is None:
        return True
    init_db(db_path)
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

