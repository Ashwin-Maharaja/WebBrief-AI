"""
WebBrief AI — Local Knowledge Retrieval & Vector Store Engine.
Provides SQLite binary float vector indexing and semantic retrieval.
Uses Google Gemini embeddings when configured, with a zero-cost deterministic
hashing fallback for unauthenticated or offline environments.
"""

import hashlib
import logging
import math
import re
import struct
from typing import Dict, Any, List, Optional, Tuple, Union

from dotenv import load_dotenv
from byok import is_serverless_env
from history import get_db_connection, init_db, get_history_entry, get_default_db_path

load_dotenv()
logger = logging.getLogger(__name__)

DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMENSIONS = 768
DEFAULT_SEARCH_LIMIT = 5
DEFAULT_SIMILARITY_THRESHOLD = 0.35


class VectorStoreError(Exception):
    """Exception raised when vector indexing or semantic search fails."""
    pass


_INITIALIZED_VECTOR_DBS: set = set()


# -------------------------------------------------------------
# Database Schema Initialization
# -------------------------------------------------------------
def init_vector_store(db_path: Optional[str] = None) -> None:
    """Initializes the summary_embeddings table inside the SQLite database."""
    if is_serverless_env() and db_path is None:
        return
    from history import get_default_db_path
    target_path = db_path or get_default_db_path()
    if target_path in _INITIALIZED_VECTOR_DBS:
        return

    try:
        init_db(db_path)
        conn = get_db_connection(db_path)
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS summary_embeddings (
                    summary_id TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    search_text TEXT NOT NULL,
                    vector_blob BLOB NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(summary_id) REFERENCES summary_history(id) ON DELETE CASCADE
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_model ON summary_embeddings(model)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_hash ON summary_embeddings(content_hash)")
        conn.close()
        _INITIALIZED_VECTOR_DBS.add(target_path)
    except Exception as e:
        _INITIALIZED_VECTOR_DBS.discard(target_path)
        logger.error("Failed to initialize vector store table at '%s': %s", target_path, str(e))
        raise VectorStoreError(f"Failed to initialize vector store table: {str(e)}") from e


# -------------------------------------------------------------
# Binary Vector Serialization & Cosine Distance
# -------------------------------------------------------------
def pack_vector(vector: List[float]) -> bytes:
    """Serializes a list of floats into a binary blob of 32-bit floats."""
    return struct.pack(f"{len(vector)}f", *vector)


def unpack_vector(blob: bytes) -> List[float]:
    """Deserializes a binary blob of 32-bit floats into a Python float list."""
    count = len(blob) // 4
    return list(struct.unpack(f"{count}f", blob))


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """
    Computes cosine similarity between two float vectors.
    Includes explicit normalization for mathematical safety.
    """
    if len(v1) != len(v2) or len(v1) == 0:
        return 0.0

    dot = 0.0
    norm1 = 0.0
    norm2 = 0.0
    for a, b in zip(v1, v2):
        dot += a * b
        norm1 += a * a
        norm2 += b * b

    if norm1 <= 0.0 or norm2 <= 0.0:
        return 0.0

    return dot / (math.sqrt(norm1) * math.sqrt(norm2))


compute_cosine_similarity = cosine_similarity


# -------------------------------------------------------------
# Content Preparation & Hashing
# -------------------------------------------------------------
def prepare_search_text(entry: Dict[str, Any]) -> str:
    """
    Constructs a clean, semantically rich text document from a saved history entry.
    Handles both standard single summaries and comparative briefs.
    """
    title = entry.get("title") or "Webpage Summary"
    url = entry.get("url") or ""
    summary = entry.get("summary") or {}
    entry_type = entry.get("entry_type") or "single"

    parts = [f"Title: {title}", f"Source URL: {url}"]

    if entry_type == "comparison":
        art_a = summary.get("article_a", {})
        art_b = summary.get("article_b", {})
        parts.append(f"Source A: {art_a.get('title', 'Article A')} ({art_a.get('url', '')})")
        if art_a.get("overview"):
            parts.append(f"Overview A: {art_a.get('overview')}")

        parts.append(f"Source B: {art_b.get('title', 'Article B')} ({art_b.get('url', '')})")
        if art_b.get("overview"):
            parts.append(f"Overview B: {art_b.get('overview')}")

        common = summary.get("common_points", [])
        if common:
            parts.append(f"Common Points: {'; '.join(str(p) for p in common)}")

        contradictions = summary.get("contradictions", [])
        if contradictions:
            parts.append(f"Contradictions & Disagreements: {'; '.join(str(c) for c in contradictions)}")

        perspectives = summary.get("differing_perspectives", [])
        if perspectives:
            parts.append(f"Differing Perspectives: {'; '.join(str(p) for p in perspectives)}")

        claims = summary.get("key_facts_claims", [])
        if claims:
            claims_str = "; ".join(f"{c.get('topic')}: Source A claims '{c.get('article_a_claim')}', Source B claims '{c.get('article_b_claim')}'" for c in claims)
            parts.append(f"Key Claims: {claims_str}")

        synthesis = summary.get("synthesis")
        if synthesis:
            parts.append(f"Synthesis: {synthesis}")

    else:
        overview = summary.get("overview") or summary.get("tldr")
        if overview:
            parts.append(f"Overview: {overview}")

        key_points = summary.get("key_points") or summary.get("key_takeaways") or []
        if key_points:
            parts.append(f"Key Takeaways: {'; '.join(str(k) for k in key_points)}")

        details = summary.get("important_details") or summary.get("core_summary") or []
        if isinstance(details, list) and details:
            parts.append(f"Important Details: {'; '.join(str(d) for d in details)}")
        elif isinstance(details, str) and details:
            parts.append(f"Important Details: {details}")

        conclusion = summary.get("conclusion")
        if conclusion:
            parts.append(f"Conclusion: {conclusion}")

    return "\n".join(parts)


def compute_content_hash(text: str) -> str:
    """Computes a SHA-256 hash of the normalized search text."""
    normalized = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _deterministic_hash_vector(text: str, dim: int = EMBEDDING_DIMENSIONS) -> List[float]:
    """
    Generates a deterministic L2-normalized 768-dim semantic hash representation.
    Used when no Gemini credentials are configured (e.g. offline mode).
    """
    words = re.findall(r"\w+", text.lower())
    if not words:
        return [0.0] * dim

    vec = [0.0] * dim
    for word in words:
        # Hash word into bucket
        h = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16)
        bucket = h % dim
        sign = 1.0 if ((h >> 16) & 1) else -1.0
        vec[bucket] += sign

    # Also hash bigrams for sequence context
    for i in range(len(words) - 1):
        bigram = f"{words[i]}_{words[i+1]}"
        h = int(hashlib.md5(bigram.encode("utf-8")).hexdigest(), 16)
        bucket = h % dim
        sign = 1.0 if ((h >> 16) & 1) else -1.0
        vec[bucket] += 0.5 * sign

    # L2 normalize
    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0:
        vec = [x / norm for x in vec]

    return vec


# -------------------------------------------------------------
# Multi-Provider Embedding Generator
# -------------------------------------------------------------
def generate_embedding_with_model(
    text: str,
    api_key: Optional[str] = None,
    model: str = DEFAULT_EMBEDDING_MODEL,
    provider_config: Optional[Dict[str, Any]] = None,
    allow_fallback: bool = True,
) -> Tuple[List[float], str]:
    """
    Generates a dense vector embedding and returns (vector, model_identifier).
    Returns real neural embeddings if credentials exist and API succeeds.
    When allow_fallback is True, falls back to deterministic hash vector on API error.
    When allow_fallback is False, raises VectorStoreError on API failure.
    """
    if not text or not str(text).strip():
        raise VectorStoreError("Cannot generate embedding for empty text.")

    from byok import get_active_provider_config
    cfg = provider_config or get_active_provider_config()

    gemini_key = api_key or cfg.get("gemini_api_key") or cfg.get("api_key")

    # If Gemini credentials exist, attempt Google Gemini embedding
    if gemini_key:
        try:
            from services.providers.gemini import GeminiProvider
            gp = GeminiProvider(api_key=gemini_key)
            vec = gp.embed_text(text=text)
            if vec:
                return vec, model
        except Exception as e:
            if not allow_fallback:
                raise VectorStoreError(f"Neural embedding generation failed: {str(e)}") from e
            logger.warning("Gemini embedding call failed; using deterministic hash fallback: %s", str(e))

    if not allow_fallback and not gemini_key:
        raise VectorStoreError("No AI provider credentials configured for neural embedding.")

    # Deterministic feature hashing fallback
    return _deterministic_hash_vector(text, dim=EMBEDDING_DIMENSIONS), "deterministic-hash-fallback"


def generate_embedding(
    text: str,
    api_key: Optional[str] = None,
    model: str = DEFAULT_EMBEDDING_MODEL,
    provider_config: Optional[Dict[str, Any]] = None,
) -> List[float]:
    """
    Generates a vector embedding for the input text using Google Gemini
    or deterministic semantic hashing fallback.
    """
    vec, _ = generate_embedding_with_model(text, api_key=api_key, model=model, provider_config=provider_config)
    return vec


# -------------------------------------------------------------
# Indexing & Cache Management
# -------------------------------------------------------------
def index_summary_entry(
    entry_id: str,
    api_key: Optional[str] = None,
    db_path: Optional[str] = None,
    force_refresh: bool = False,
    provider_config: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Generates and stores the vector embedding for a single saved summary history entry.
    Skips generation if an identical content hash is already indexed.
    Returns True if indexed or skipped, False on failure.
    """
    init_vector_store(db_path)
    entry = get_history_entry(entry_id, db_path=db_path)
    if not entry:
        logger.warning("Cannot index missing history entry: %s", entry_id)
        return False

    search_text = prepare_search_text(entry)
    content_hash = compute_content_hash(search_text)

    try:
        conn = get_db_connection(db_path)
        cursor = conn.cursor()

        if not force_refresh:
            cursor.execute(
                "SELECT content_hash FROM summary_embeddings WHERE summary_id = ?",
                (entry_id,)
            )
            row = cursor.fetchone()
            if row and row["content_hash"] == content_hash:
                conn.close()
                return True

        vector, actual_model = generate_embedding_with_model(
            search_text, api_key=api_key, model=DEFAULT_EMBEDDING_MODEL, provider_config=provider_config
        )
        vector_blob = pack_vector(vector)

        with conn:
            conn.execute(
                """
                INSERT INTO summary_embeddings (
                    summary_id, model, dimensions, content_hash, search_text, vector_blob, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(summary_id) DO UPDATE SET
                    model = excluded.model,
                    dimensions = excluded.dimensions,
                    content_hash = excluded.content_hash,
                    search_text = excluded.search_text,
                    vector_blob = excluded.vector_blob,
                    created_at = CURRENT_TIMESTAMP
                """,
                (
                    entry_id,
                    actual_model,
                    len(vector),
                    content_hash,
                    search_text,
                    vector_blob,
                )
            )
        conn.close()
        return True

    except Exception as e:
        logger.warning("Failed to index summary '%s': %s", entry_id, str(e))
        return False


def delete_embedding(summary_id: str, db_path: Optional[str] = None) -> bool:
    """Deletes an embedding associated with a summary ID."""
    init_vector_store(db_path)
    try:
        conn = get_db_connection(db_path)
        with conn:
            cursor = conn.execute("DELETE FROM summary_embeddings WHERE summary_id = ?", (summary_id,))
            affected = cursor.rowcount
        conn.close()
        return affected > 0
    except Exception as e:
        logger.warning("Failed to delete embedding for '%s': %s", summary_id, str(e))
        return False


def clear_all_embeddings(db_path: Optional[str] = None) -> bool:
    """Clears all vectors from the summary_embeddings table."""
    init_vector_store(db_path)
    try:
        conn = get_db_connection(db_path)
        with conn:
            conn.execute("DELETE FROM summary_embeddings")
        conn.close()
        return True
    except Exception as e:
        logger.warning("Failed to clear embeddings: %s", str(e))
        return False


def cleanup_orphaned_vectors(db_path: Optional[str] = None) -> int:
    """
    Deletes orphaned embedding records where the associated summary_history record no longer exists (P2-SRCH-02).
    Returns the count of deleted orphaned rows.
    """
    if is_serverless_env() and db_path is None:
        return 0

    init_vector_store(db_path)
    try:
        conn = get_db_connection(db_path)
        with conn:
            cursor = conn.execute("""
                DELETE FROM summary_embeddings
                WHERE summary_id NOT IN (SELECT id FROM summary_history)
            """)
            deleted = cursor.rowcount
        conn.close()
        return deleted
    except Exception as e:
        logger.warning("Failed to cleanup orphaned vectors: %s", str(e))
        return 0


def rebuild_vector_index(
    api_key: Optional[str] = None,
    db_path: Optional[str] = None,
    provider_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Rebuilds vector embeddings across ALL saved summaries in SQLite history without an arbitrary limit."""
    init_vector_store(db_path)
    # Purge any orphaned vectors first (P2-SRCH-02)
    orphaned_cleaned = cleanup_orphaned_vectors(db_path=db_path)
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM summary_history ORDER BY created_at ASC")
    rows = cursor.fetchall()
    conn.close()

    total = len(rows)
    indexed = 0
    failed = 0

    for r in rows:
        entry_id = r["id"]
        try:
            success = index_summary_entry(
                entry_id,
                api_key=api_key,
                db_path=db_path,
                force_refresh=True,
                provider_config=provider_config,
            )
            if success:
                indexed += 1
            else:
                failed += 1
        except Exception:
            failed += 1

    return {
        "total": total,
        "indexed": indexed,
        "failed": failed,
        "orphaned_cleaned": orphaned_cleaned,
        "model": DEFAULT_EMBEDDING_MODEL,
        "dimensions": EMBEDDING_DIMENSIONS,
    }


def get_vector_store_stats(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Returns vector storage metadata, model breakdown, and total indexed count."""
    init_vector_store(db_path)
    try:
        conn = get_db_connection(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) AS cnt FROM summary_embeddings")
        cnt = cursor.fetchone()["cnt"]

        cursor.execute("SELECT model, dimensions, COUNT(*) AS cnt FROM summary_embeddings GROUP BY model, dimensions")
        group_rows = cursor.fetchall()
        conn.close()

        breakdown = {
            f"{r['model']}:{r['dimensions']}": r["cnt"] for r in group_rows
        }
        primary_model = group_rows[0]["model"] if group_rows else DEFAULT_EMBEDDING_MODEL
        primary_dims = group_rows[0]["dimensions"] if group_rows else EMBEDDING_DIMENSIONS

        return {
            "indexed_count": cnt,
            "model": primary_model,
            "dimensions": primary_dims,
            "model_breakdown": breakdown,
            "status": "ready" if cnt > 0 else "empty"
        }
    except Exception as e:
        return {
            "indexed_count": 0,
            "model": DEFAULT_EMBEDDING_MODEL,
            "dimensions": EMBEDDING_DIMENSIONS,
            "model_breakdown": {},
            "status": f"error: {str(e)}"
        }


def extract_relevant_snippet(search_text: str, query: str, max_chars: int = 240) -> str:
    """Extracts the most relevant paragraph or sentence from indexed summary text."""
    if not search_text:
        return ""

    paragraphs = [p.strip() for p in search_text.split("\n") if p.strip() and not p.startswith("Title:") and not p.startswith("Source URL:")]
    if not paragraphs:
        paragraphs = [search_text]

    query_words = set(re.findall(r"\w+", query.lower()))
    best_p = paragraphs[0]
    best_score = -1

    for p in paragraphs:
        p_words = set(re.findall(r"\w+", p.lower()))
        overlap = len(query_words.intersection(p_words))
        if overlap > best_score:
            best_score = overlap
            best_p = p

    clean_p = re.sub(r"^(Overview|Key Takeaways|Important Details|Conclusion|Synthesis):\s*", "", best_p)
    if len(clean_p) > max_chars:
        return clean_p[:max_chars].rsplit(" ", 1)[0] + "..."
    return clean_p


def semantic_search(
    query: str,
    api_key: Optional[str] = None,
    top_k: int = DEFAULT_SEARCH_LIMIT,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    entry_type_filter: Optional[str] = None,
    filter_type: Optional[str] = None,
    db_path: Optional[str] = None,
    provider_config: Optional[Dict[str, Any]] = None,
    return_metadata: bool = False,
) -> Union[List[Dict[str, Any]], Tuple[List[Dict[str, Any]], Dict[str, Any]]]:
    """
    Executes semantic similarity search across saved summary embeddings.
    When return_metadata=True, returns (results, metadata_dict) where metadata_dict contains
    search_mode, embedding_model, and status_detail (P2-SRCH-03, P2-SRCH-04).
    """
    cleaned_query = (query or "").strip()
    if not cleaned_query:
        if return_metadata:
            return [], {"search_mode": "none", "embedding_model": None, "status_detail": "empty_query"}
        return []

    active_filter = filter_type or entry_type_filter
    init_vector_store(db_path)

    query_vector, query_model = generate_embedding_with_model(
        cleaned_query, api_key=api_key, model=DEFAULT_EMBEDDING_MODEL, provider_config=provider_config
    )
    query_dims = len(query_vector)
    search_mode = "hash_vector" if query_model == "deterministic-hash-fallback" else "semantic"

    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT e.summary_id, e.model, e.dimensions, e.search_text, e.vector_blob,
               h.title, h.url, h.mode, h.mode_name, h.entry_type, h.url_b, h.title_b, h.created_at_formatted
        FROM summary_embeddings e
        JOIN summary_history h ON e.summary_id = h.id
    """)
    rows = cursor.fetchall()

    if not rows:
        try:
            cursor.execute("SELECT COUNT(*) AS cnt FROM summary_history")
            hist_count = cursor.fetchone()["cnt"]
            cursor.execute("SELECT COUNT(*) AS cnt FROM summary_embeddings")
            embed_count = cursor.fetchone()["cnt"]
        except Exception:
            hist_count = 0
            embed_count = 0
        conn.close()

        status_detail = "no_records" if hist_count == 0 else ("index_empty" if embed_count == 0 else "no_matching_records")
        if return_metadata:
            return [], {
                "search_mode": search_mode,
                "embedding_model": query_model,
                "status_detail": status_detail,
            }
        return []

    conn.close()

    scored_candidates = []
    compatible_count = 0
    filtered_count = 0

    for r in rows:
        # Enforce model and dimension compatibility (SRCH-01)
        r_model = r["model"]
        r_dims = r["dimensions"]
        if r_model != query_model or r_dims != query_dims:
            continue
        compatible_count += 1

        r_type = r["entry_type"] or "single"
        if active_filter and active_filter != "all" and r_type != active_filter:
            continue
        filtered_count += 1

        try:
            stored_vector = unpack_vector(r["vector_blob"])
            score = cosine_similarity(query_vector, stored_vector)
            if score >= threshold:
                snippet = extract_relevant_snippet(r["search_text"], cleaned_query)
                scored_candidates.append({
                    "id": r["summary_id"],
                    "title": r["title"],
                    "url": r["url"],
                    "url_b": r["url_b"],
                    "title_b": r["title_b"],
                    "mode": r["mode"],
                    "mode_name": r["mode_name"],
                    "entry_type": r_type,
                    "model": r_model,
                    "dimensions": r_dims,
                    "created_at_formatted": r["created_at_formatted"],
                    "score": round(score, 4),
                    "similarity": round(score, 4),
                    "match_percentage": int(round(score * 100)),
                    "snippet": snippet,
                    "relevant_snippet": snippet,
                })
        except Exception as e:
            logger.warning("Error evaluating vector for '%s': %s", r["summary_id"], str(e))
            continue

    scored_candidates.sort(key=lambda x: x["score"], reverse=True)
    results = scored_candidates[:top_k]

    if results:
        status_detail = "ready"
    elif compatible_count == 0:
        status_detail = "no_compatible_vectors"
    elif filtered_count == 0:
        status_detail = "filter_no_match"
    else:
        status_detail = "below_threshold"

    if return_metadata:
        return results, {
            "search_mode": search_mode,
            "embedding_model": query_model,
            "status_detail": status_detail,
        }
    return results
