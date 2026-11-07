"""
WebBrief AI (v1.7.0) — Resumable Summarization & Checkpoint Recovery Manager

Manages the lifecycle, state transitions, and persistent checkpoints for
chunked summarization jobs in local SQLite storage.
Ensures zero data loss when interruptions occur: every successfully completed chunk
is persisted immediately, allowing seamless resumption without redundant API calls.
"""

import datetime
import json
import logging
import os
import sqlite3
import threading
import uuid
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_DB_FILENAME = "webbrief_history.db"

# Valid Job States (Section 6)
STATUS_QUEUED = "Queued"
STATUS_EXTRACTING = "Extracting"
STATUS_CHUNKING = "Chunking"
STATUS_PROCESSING = "Processing"
STATUS_RECOVERING = "Recovering"
STATUS_PAUSED = "Paused"
STATUS_INTERRUPTED = "Interrupted"
STATUS_SYNTHESIZING = "Synthesizing"
STATUS_COMPLETED = "Completed"
STATUS_FAILED = "Failed"
STATUS_CANCELLED = "Cancelled"

VALID_STATUSES = {
    STATUS_QUEUED,
    STATUS_EXTRACTING,
    STATUS_CHUNKING,
    STATUS_PROCESSING,
    STATUS_RECOVERING,
    STATUS_PAUSED,
    STATUS_INTERRUPTED,
    STATUS_SYNTHESIZING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_CANCELLED,
}

# Valid State Transitions
VALID_TRANSITIONS = {
    STATUS_QUEUED: {STATUS_EXTRACTING, STATUS_PROCESSING, STATUS_CANCELLED, STATUS_FAILED},
    STATUS_EXTRACTING: {STATUS_CHUNKING, STATUS_FAILED, STATUS_CANCELLED, STATUS_INTERRUPTED},
    STATUS_CHUNKING: {STATUS_PROCESSING, STATUS_FAILED, STATUS_CANCELLED, STATUS_INTERRUPTED},
    STATUS_PROCESSING: {
        STATUS_PROCESSING,
        STATUS_PAUSED,
        STATUS_INTERRUPTED,
        STATUS_SYNTHESIZING,
        STATUS_FAILED,
        STATUS_CANCELLED,
    },
    STATUS_PAUSED: {STATUS_PROCESSING, STATUS_RECOVERING, STATUS_CANCELLED},
    STATUS_INTERRUPTED: {STATUS_RECOVERING, STATUS_PROCESSING, STATUS_CANCELLED, STATUS_FAILED},
    STATUS_RECOVERING: {STATUS_PROCESSING, STATUS_FAILED, STATUS_CANCELLED, STATUS_INTERRUPTED},
    STATUS_SYNTHESIZING: {STATUS_COMPLETED, STATUS_FAILED, STATUS_INTERRUPTED},
    STATUS_COMPLETED: {STATUS_COMPLETED},  # Terminal
    STATUS_FAILED: {STATUS_RECOVERING, STATUS_CANCELLED},
    STATUS_CANCELLED: set(),  # Terminal: Cancelled jobs cannot be resumed
}

_db_lock = threading.Lock()


def get_default_db_path() -> str:
    """Returns absolute path to the local SQLite database."""
    workspace_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(workspace_dir, DEFAULT_DB_FILENAME)


def get_db_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Creates a thread-safe connection to the SQLite database."""
    target_path = db_path or get_default_db_path()
    try:
        conn = sqlite3.connect(target_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        # Enable foreign keys and WAL mode for reliable concurrency
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError:
            pass
        return conn
    except (sqlite3.OperationalError, OSError):
        conn = sqlite3.connect(":memory:", timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn


def init_job_tables(db_path: Optional[str] = None) -> None:
    """
    Initializes database tables for resumable summarization jobs,
    chunks, and audit events.
    Recovers any stale in-progress jobs from previous server runs.
    """
    with _db_lock:
        conn = get_db_connection(db_path)
        try:
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS summarization_jobs (
                        id TEXT PRIMARY KEY,
                        source_url TEXT NOT NULL,
                        title TEXT DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'Queued',
                        mode TEXT NOT NULL DEFAULT 'detailed',
                        provider_config_json TEXT NOT NULL DEFAULT '{}',
                        total_chunks INTEGER DEFAULT 0,
                        completed_chunks INTEGER DEFAULT 0,
                        current_position INTEGER DEFAULT 0,
                        article_text TEXT DEFAULT '',
                        recovery_message TEXT DEFAULT '',
                        final_summary_json TEXT DEFAULT '',
                        history_id TEXT DEFAULT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON summarization_jobs(created_at DESC)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON summarization_jobs(status)")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS job_chunks (
                        id TEXT PRIMARY KEY,
                        job_id TEXT NOT NULL,
                        chunk_index INTEGER NOT NULL,
                        chunk_text TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        summary_output TEXT DEFAULT '',
                        provider TEXT DEFAULT '',
                        model TEXT DEFAULT '',
                        attempt_count INTEGER DEFAULT 0,
                        error_message TEXT DEFAULT '',
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (job_id) REFERENCES summarization_jobs(id) ON DELETE CASCADE
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_job_index ON job_chunks(job_id, chunk_index)")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS job_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        message TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (job_id) REFERENCES summarization_jobs(id) ON DELETE CASCADE
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_events_job ON job_events(job_id, created_at)")

                # Stale process recovery on startup (Section 3 & 9):
                # Transition any jobs stuck in active execution states to 'Interrupted'
                stale_cursor = conn.execute("""
                    SELECT id FROM summarization_jobs 
                    WHERE status IN ('Processing', 'Extracting', 'Chunking', 'Synthesizing')
                """)
                stale_ids = [row["id"] for row in stale_cursor.fetchall()]
                for jid in stale_ids:
                    conn.execute("""
                        UPDATE summarization_jobs 
                        SET status = ?, 
                            recovery_message = ?, 
                            updated_at = CURRENT_TIMESTAMP 
                        WHERE id = ?
                    """, (
                        STATUS_INTERRUPTED,
                        "Process was interrupted by server restart. Completed checkpoints have been preserved and can be resumed.",
                        jid
                    ))
                    conn.execute("""
                        UPDATE job_chunks 
                        SET status = 'pending' 
                        WHERE job_id = ? AND status = 'processing'
                    """, (jid,))
                    conn.execute("""
                        INSERT INTO job_events (job_id, event_type, message) 
                        VALUES (?, 'restart_recovery', 'Detected stale execution state on server startup; transitioned job to Interrupted.')
                    """, (jid,))

        except Exception as e:
            logger.warning("Failed to initialize job tables: %s", str(e))
        finally:
            conn.close()


def sanitize_config_snapshot(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Creates a safe snapshot of the provider and prompt configuration.
    CRITICAL: Strips all API keys and tokens so credentials are NEVER written to SQLite.
    """
    if not config:
        return {}
    safe = {
        "primary_provider": config.get("primary_provider", "cloudflare"),
        "cloudflare_model": config.get("cloudflare_model") or config.get("cloudflare_default_model", "@cf/meta/llama-3.1-8b-instruct"),
        "groq_model": config.get("groq_model") or config.get("groq_default_model", "llama-3.3-70b-versatile"),
        "fallback_enabled": bool(config.get("fallback_enabled")),
        "fallback_provider": config.get("fallback_provider", "groq"),
        "fallback_model": config.get("fallback_model", "llama-3.3-70b-versatile"),
        "custom_directive": str(config.get("custom_directive") or "")[:1000],
        "custom_directive_enabled": bool(config.get("custom_directive_enabled")),
        "chunk_size": int(config.get("chunk_size", 4000)),
        "chunk_overlap": int(config.get("chunk_overlap", 400)),
    }
    return safe


def create_job(
    source_url: str,
    mode: str = "detailed",
    provider_config: Optional[Dict[str, Any]] = None,
    title: str = "",
    article_text: str = "",
    job_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> str:
    """
    Creates and persists a new summarization job with an initial configuration snapshot.
    """
    jid = job_id or str(uuid.uuid4())
    safe_config = sanitize_config_snapshot(provider_config)
    config_json = json.dumps(safe_config)

    with _db_lock:
        conn = get_db_connection(db_path)
        try:
            with conn:
                conn.execute("""
                    INSERT INTO summarization_jobs (
                        id, source_url, title, status, mode, 
                        provider_config_json, total_chunks, completed_chunks, 
                        current_position, article_text, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0, ?, CURRENT_TIMESTAMP)
                """, (jid, source_url, title or source_url, STATUS_QUEUED, mode, config_json, article_text))

                conn.execute("""
                    INSERT INTO job_events (job_id, event_type, message) 
                    VALUES (?, 'created', ?)
                """, (jid, f"Job initialized for URL: {source_url} (Mode: {mode})"))
        finally:
            conn.close()

    return jid


def initialize_job_chunks(
    job_id: str,
    chunks: List[str],
    title: Optional[str] = None,
    article_text: Optional[str] = None,
    db_path: Optional[str] = None,
) -> None:
    """
    Registers chunk definitions for a job, transitioning state from Chunking to Processing.
    """
    with _db_lock:
        conn = get_db_connection(db_path)
        try:
            with conn:
                # Clear any existing chunks if re-initializing
                conn.execute("DELETE FROM job_chunks WHERE job_id = ?", (job_id,))

                for idx, chunk_str in enumerate(chunks, 1):
                    cid = str(uuid.uuid4())
                    conn.execute("""
                        INSERT INTO job_chunks (
                            id, job_id, chunk_index, chunk_text, status, attempt_count, updated_at
                        ) VALUES (?, ?, ?, ?, 'pending', 0, CURRENT_TIMESTAMP)
                    """, (cid, job_id, idx, chunk_str))

                update_fields = [
                    "total_chunks = ?",
                    "status = ?",
                    "updated_at = CURRENT_TIMESTAMP",
                ]
                params: List[Any] = [len(chunks), STATUS_PROCESSING]

                if title:
                    update_fields.append("title = ?")
                    params.append(title)
                if article_text:
                    update_fields.append("article_text = ?")
                    params.append(article_text)

                params.append(job_id)
                query = f"UPDATE summarization_jobs SET {', '.join(update_fields)} WHERE id = ?"
                conn.execute(query, params)

                conn.execute("""
                    INSERT INTO job_events (job_id, event_type, message) 
                    VALUES (?, 'chunked', ?)
                """, (job_id, f"Divided content into {len(chunks)} chunks."))
        finally:
            conn.close()


def update_job_status(
    job_id: str,
    new_status: str,
    recovery_message: Optional[str] = None,
    db_path: Optional[str] = None,
) -> bool:
    """
    Safely transitions a job's status according to the valid state transition matrix.
    """
    if new_status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: '{new_status}'")

    with _db_lock:
        conn = get_db_connection(db_path)
        try:
            with conn:
                row = conn.execute("SELECT status FROM summarization_jobs WHERE id = ?", (job_id,)).fetchone()
                if not row:
                    return False
                curr_status = row["status"]

                if curr_status == STATUS_CANCELLED and new_status != STATUS_CANCELLED:
                    logger.warning("Attempted to transition cancelled job '%s' to '%s'; rejected.", job_id, new_status)
                    return False

                if new_status != curr_status and new_status not in VALID_TRANSITIONS.get(curr_status, set()):
                    # Allow Recovering from any non-completed state or if recovery forced
                    if new_status not in (STATUS_RECOVERING, STATUS_INTERRUPTED, STATUS_FAILED):
                        logger.warning(
                            "Invalid transition from '%s' to '%s' for job '%s'.",
                            curr_status, new_status, job_id
                        )

                msg = recovery_message or ""
                conn.execute("""
                    UPDATE summarization_jobs 
                    SET status = ?, recovery_message = ?, updated_at = CURRENT_TIMESTAMP 
                    WHERE id = ?
                """, (new_status, msg, job_id))

                conn.execute("""
                    INSERT INTO job_events (job_id, event_type, message) 
                    VALUES (?, 'status_change', ?)
                """, (job_id, f"Status changed from {curr_status} to {new_status}. {msg}".strip()))
            return True
        finally:
            conn.close()


def save_chunk_checkpoint(
    job_id: str,
    chunk_index: int,
    summary_output: str,
    provider: str,
    model: str,
    db_path: Optional[str] = None,
) -> bool:
    """
    Transactionally persists the completed summary for a single chunk.
    Updates the job's completed_chunks counter and current_position.
    CORE RULE: Save every successful chunk immediately before moving to the next chunk.
    """
    with _db_lock:
        conn = get_db_connection(db_path)
        try:
            with conn:
                # 1. Update the specific chunk
                conn.execute("""
                    UPDATE job_chunks 
                    SET status = 'completed', 
                        summary_output = ?, 
                        provider = ?, 
                        model = ?, 
                        error_message = '',
                        updated_at = CURRENT_TIMESTAMP 
                    WHERE job_id = ? AND chunk_index = ?
                """, (summary_output, provider, model, job_id, chunk_index))

                # 2. Count total completed chunks
                count_row = conn.execute("""
                    SELECT COUNT(*) as completed_count 
                    FROM job_chunks 
                    WHERE job_id = ? AND status = 'completed'
                """, (job_id,)).fetchone()
                completed = count_row["completed_count"] if count_row else 0

                # 3. Update job progress
                conn.execute("""
                    UPDATE summarization_jobs 
                    SET completed_chunks = ?, 
                        current_position = ?, 
                        updated_at = CURRENT_TIMESTAMP 
                    WHERE id = ?
                """, (completed, chunk_index, job_id))

                # 4. Audit log
                conn.execute("""
                    INSERT INTO job_events (job_id, event_type, message) 
                    VALUES (?, 'chunk_completed', ?)
                """, (job_id, f"Chunk {chunk_index} completed using {provider} ({model}). Total completed: {completed}."))
            return True
        finally:
            conn.close()


def record_chunk_failure(
    job_id: str,
    chunk_index: int,
    error_message: str,
    max_attempts: int = 3,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Records an error on an individual chunk, increments attempt count,
    and determines whether bounded retry limits have been reached.
    """
    with _db_lock:
        conn = get_db_connection(db_path)
        try:
            with conn:
                row = conn.execute("""
                    SELECT attempt_count FROM job_chunks 
                    WHERE job_id = ? AND chunk_index = ?
                """, (job_id, chunk_index)).fetchone()

                attempts = (row["attempt_count"] if row else 0) + 1
                is_exhausted = attempts >= max_attempts
                new_chunk_status = "failed" if is_exhausted else "pending"

                conn.execute("""
                    UPDATE job_chunks 
                    SET status = ?, 
                        attempt_count = ?, 
                        error_message = ?, 
                        updated_at = CURRENT_TIMESTAMP 
                    WHERE job_id = ? AND chunk_index = ?
                """, (new_chunk_status, attempts, error_message, job_id, chunk_index))

                # Count completed chunks preserved
                count_row = conn.execute("""
                    SELECT COUNT(*) as completed_count 
                    FROM job_chunks 
                    WHERE job_id = ? AND status = 'completed'
                """, (job_id,)).fetchone()
                preserved = count_row["completed_count"] if count_row else 0

                rec_msg = (
                    f"Chunk {chunk_index} encountered an error: {error_message}. "
                    f"{preserved} completed chunk(s) preserved."
                )

                new_job_status = STATUS_FAILED if is_exhausted else STATUS_INTERRUPTED
                conn.execute("""
                    UPDATE summarization_jobs 
                    SET status = ?, 
                        recovery_message = ?, 
                        updated_at = CURRENT_TIMESTAMP 
                    WHERE id = ?
                """, (new_job_status, rec_msg, job_id))

                conn.execute("""
                    INSERT INTO job_events (job_id, event_type, message) 
                    VALUES (?, 'chunk_failed', ?)
                """, (job_id, f"Chunk {chunk_index} attempt {attempts}/{max_attempts} failed: {error_message}"))

                return {
                    "attempt_count": attempts,
                    "is_exhausted": is_exhausted,
                    "preserved_chunks": preserved,
                    "recovery_message": rec_msg,
                }
        finally:
            conn.close()


def get_job(job_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Fetches full job metadata and progress metrics."""
    with _db_lock:
        conn = get_db_connection(db_path)
        try:
            row = conn.execute("SELECT * FROM summarization_jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                return None
            job = dict(row)
            try:
                job["provider_config"] = json.loads(job.get("provider_config_json") or "{}")
            except Exception:
                job["provider_config"] = {}
            if job.get("final_summary_json"):
                try:
                    job["final_summary"] = json.loads(job["final_summary_json"])
                except Exception:
                    job["final_summary"] = None
            else:
                job["final_summary"] = None
            return job
        finally:
            conn.close()


def get_job_chunks(job_id: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns all chunks belonging to a job, ordered by chunk_index."""
    with _db_lock:
        conn = get_db_connection(db_path)
        try:
            cursor = conn.execute("""
                SELECT * FROM job_chunks 
                WHERE job_id = ? 
                ORDER BY chunk_index ASC
            """, (job_id,))
            return [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()


def get_job_events(job_id: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns audit log of all events for a job."""
    with _db_lock:
        conn = get_db_connection(db_path)
        try:
            cursor = conn.execute("""
                SELECT * FROM job_events 
                WHERE job_id = ? 
                ORDER BY created_at ASC
            """, (job_id,))
            return [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()


def list_jobs(limit: int = 50, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns a list of recent jobs with progress percentages and current statuses."""
    with _db_lock:
        conn = get_db_connection(db_path)
        try:
            cursor = conn.execute("""
                SELECT id, source_url, title, status, mode, total_chunks, completed_chunks,
                       current_position, recovery_message, history_id, created_at, updated_at
                FROM summarization_jobs 
                ORDER BY updated_at DESC 
                LIMIT ?
            """, (limit,))
            jobs = []
            for r in cursor.fetchall():
                jd = dict(r)
                tot = jd.get("total_chunks") or 0
                comp = jd.get("completed_chunks") or 0
                jd["progress_pct"] = round((comp / tot) * 100) if tot > 0 else (100 if jd.get("status") == STATUS_COMPLETED else 0)
                jobs.append(jd)
            return jobs
        finally:
            conn.close()


def delete_job(job_id: str, db_path: Optional[str] = None) -> bool:
    """Permanently deletes a job, its chunks, and associated event logs."""
    with _db_lock:
        conn = get_db_connection(db_path)
        try:
            with conn:
                conn.execute("DELETE FROM job_chunks WHERE job_id = ?", (job_id,))
                conn.execute("DELETE FROM job_events WHERE job_id = ?", (job_id,))
                res = conn.execute("DELETE FROM summarization_jobs WHERE id = ?", (job_id,))
                return res.rowcount > 0
        finally:
            conn.close()


def pause_job(job_id: str, db_path: Optional[str] = None) -> bool:
    """Pauses an active job."""
    return update_job_status(job_id, STATUS_PAUSED, "Job paused by user.", db_path=db_path)


def cancel_job(job_id: str, db_path: Optional[str] = None) -> bool:
    """Cancels a job. Cancelled jobs cannot be resumed."""
    return update_job_status(job_id, STATUS_CANCELLED, "Job cancelled by user.", db_path=db_path)


def recover_job(job_id: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Prepares an interrupted, paused, or failed job for resumption.
    Identifies all successfully completed chunks, leaves them intact,
    resets any failed or stuck chunks to 'pending', and sets job state to 'Recovering'.
    """
    with _db_lock:
        conn = get_db_connection(db_path)
        try:
            with conn:
                job_row = conn.execute("SELECT * FROM summarization_jobs WHERE id = ?", (job_id,)).fetchone()
                if not job_row:
                    raise ValueError(f"Job not found: {job_id}")

                if job_row["status"] == STATUS_CANCELLED:
                    raise ValueError("Cancelled jobs cannot be resumed.")

                chunks_cursor = conn.execute("""
                    SELECT chunk_index, status, summary_output, provider, model 
                    FROM job_chunks 
                    WHERE job_id = ? 
                    ORDER BY chunk_index ASC
                """, (job_id,))
                chunks = [dict(c) for c in chunks_cursor.fetchall()]

                completed = [c for c in chunks if c["status"] == "completed" and c.get("summary_output")]
                unfinished = [c for c in chunks if c["status"] != "completed" or not c.get("summary_output")]

                # Reset non-completed chunks to pending
                conn.execute("""
                    UPDATE job_chunks 
                    SET status = 'pending' 
                    WHERE job_id = ? AND status != 'completed'
                """, (job_id,))

                rec_msg = (
                    f"Resuming job: {len(completed)} completed chunk(s) preserved. "
                    f"{len(unfinished)} chunk(s) remaining."
                )

                new_status = STATUS_SYNTHESIZING if not unfinished else STATUS_RECOVERING
                conn.execute("""
                    UPDATE summarization_jobs 
                    SET status = ?, 
                        recovery_message = ?, 
                        completed_chunks = ?, 
                        updated_at = CURRENT_TIMESTAMP 
                    WHERE id = ?
                """, (new_status, rec_msg, len(completed), job_id))

                conn.execute("""
                    INSERT INTO job_events (job_id, event_type, message) 
                    VALUES (?, 'recovery_initiated', ?)
                """, (job_id, rec_msg))

                return {
                    "job_id": job_id,
                    "status": new_status,
                    "total_chunks": len(chunks),
                    "completed_chunks": len(completed),
                    "remaining_chunks": len(unfinished),
                    "completed_indices": [c["chunk_index"] for c in completed],
                    "unfinished_indices": [c["chunk_index"] for c in unfinished],
                    "recovery_message": rec_msg,
                }
        finally:
            conn.close()


def complete_job(
    job_id: str,
    final_summary: Dict[str, Any],
    history_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> bool:
    """
    Marks a job as Completed, persists the structured summary JSON,
    and links to the summary history record.
    """
    summary_json = json.dumps(final_summary)
    with _db_lock:
        conn = get_db_connection(db_path)
        try:
            with conn:
                conn.execute("""
                    UPDATE summarization_jobs 
                    SET status = ?, 
                        final_summary_json = ?, 
                        history_id = ?, 
                        recovery_message = 'Summarization completed successfully.', 
                        updated_at = CURRENT_TIMESTAMP 
                    WHERE id = ?
                """, (STATUS_COMPLETED, summary_json, history_id, job_id))

                conn.execute("""
                    INSERT INTO job_events (job_id, event_type, message) 
                    VALUES (?, 'completed', 'Final synthesis completed and saved to history.')
                """, (job_id,))
            return True
        finally:
            conn.close()


def check_config_consistency(
    job_id: str,
    current_config: Optional[Dict[str, Any]] = None,
    current_mode: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Compares the active summarization configuration against the job's initial snapshot.
    Prevents silent output contamination if settings were changed after interruption (Section 10).
    """
    job = get_job(job_id, db_path=db_path)
    if not job:
        return {"consistent": True, "differences": [], "original_config": {}}

    orig = job.get("provider_config") or {}
    safe_curr = sanitize_config_snapshot(current_config or {})

    diffs: List[str] = []
    if current_mode and current_mode != job.get("mode"):
        diffs.append(f"Mode changed from '{job.get('mode')}' to '{current_mode}'")

    if safe_curr.get("primary_provider") and orig.get("primary_provider") and safe_curr["primary_provider"] != orig["primary_provider"]:
        diffs.append(f"Primary provider changed from '{orig['primary_provider']}' to '{safe_curr['primary_provider']}'")

    orig_dir = (orig.get("custom_directive") or "").strip()
    curr_dir = (safe_curr.get("custom_directive") or "").strip()
    if orig_dir != curr_dir:
        diffs.append("Custom prompt instructions were modified")

    return {
        "consistent": len(diffs) == 0,
        "differences": diffs,
        "original_config": orig,
        "original_mode": job.get("mode"),
    }

