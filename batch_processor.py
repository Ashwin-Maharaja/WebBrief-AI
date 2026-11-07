"""
WebBrief AI — Batch Summarization Processor
Coordinates concurrent, resilient multi-URL scraping and summarization.
Handles duplicate detection, independent validation, per-URL failure isolation,
sensible concurrency limits, and history persistence.
"""

import concurrent.futures
import logging
import urllib.parse
from typing import List, Dict, Any, Tuple, Optional, Callable

from scraper import scrape_article, validate_safe_url, ScraperError
from summarizer import summarize_text, SummarizerError, MODES, DEFAULT_MODE
from history import add_history_entry, get_custom_directives
from byok import is_serverless_env
import uuid

logger = logging.getLogger(__name__)

MAX_BATCH_URLS = 10
DEFAULT_CONCURRENCY = 2


def validate_batch_urls(raw_urls: List[str]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Validates a list of raw URL strings independently.
    Detects duplicates and format errors without failing the entire batch.

    Returns:
        (validated_items, stats):
            validated_items: List of dicts with keys:
                - index: int
                - original_url: str
                - normalized_url: str (or None)
                - is_valid: bool
                - is_duplicate: bool
                - error: str (or None)
            stats: summary counts
    """
    if not raw_urls:
        return [], {"total": 0, "valid": 0, "invalid": 0, "duplicates": 0}

    # Limit to MAX_BATCH_URLS
    urls_to_check = raw_urls[:MAX_BATCH_URLS]

    validated = []
    seen_urls = {}  # normalized_url -> first index
    stats = {"total": len(urls_to_check), "valid": 0, "invalid": 0, "duplicates": 0}

    for idx, raw in enumerate(urls_to_check):
        cleaned = (raw or "").strip()
        item = {
            "index": idx,
            "original_url": cleaned,
            "normalized_url": None,
            "is_valid": False,
            "is_duplicate": False,
            "error": None,
        }

        if not cleaned:
            item["error"] = "URL cannot be empty."
            stats["invalid"] += 1
            validated.append(item)
            continue

        try:
            safe_url = validate_safe_url(cleaned)
            item["normalized_url"] = safe_url

            # Duplicate check
            norm_key = safe_url.lower().rstrip("/")
            if norm_key in seen_urls:
                item["is_duplicate"] = True
                first_idx = seen_urls[norm_key]
                item["error"] = f"Duplicate of URL #{first_idx + 1}"
                stats["duplicates"] += 1
            else:
                seen_urls[norm_key] = idx
                item["is_valid"] = True
                stats["valid"] += 1

        except ScraperError as se:
            item["error"] = str(se)
            stats["invalid"] += 1
        except Exception as e:
            item["error"] = f"Invalid URL: {str(e)}"
            stats["invalid"] += 1

        validated.append(item)

    return validated, stats


def process_single_item(
    url: str,
    mode: str = DEFAULT_MODE,
    api_key: Optional[str] = None,
    custom_directive: Optional[str] = None,
    custom_directive_enabled: Optional[bool] = None,
    record_history: bool = True,
    batch_id: Optional[str] = None,
    provider_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Executes scraping and structured summarization for a single URL within a batch.
    Isolates errors so that failures on one URL do not impact others.
    """
    active_mode = (mode or DEFAULT_MODE).strip().lower()
    if active_mode not in MODES:
        active_mode = DEFAULT_MODE

    result_item = {
        "url": url,
        "mode": active_mode,
        "mode_name": MODES[active_mode]["name"],
        "status": "pending",
        "title": None,
        "summary": None,
        "error": None,
        "history_id": None,
        "batch_id": batch_id,
    }

    try:
        # Step 1: Validate URL for security
        safe_url = validate_safe_url(url)
        result_item["url"] = safe_url

        # Step 2: Extract content
        article_text = scrape_article(safe_url)

        # Step 3: Summarize content
        summary = summarize_text(
            article_text,
            api_key=api_key,
            mode=active_mode,
            custom_directive=custom_directive,
            custom_directive_enabled=custom_directive_enabled,
            provider_config=provider_config,
        )

        result_item["status"] = "completed"
        result_item["title"] = summary.get("title") or "Webpage Summary"
        result_item["summary"] = summary

        # Step 4: Record history entry individually (Local mode only — hosted mode relies on client IndexedDB)
        if record_history and not is_serverless_env():
            try:
                hist_entry = add_history_entry(
                    title=result_item["title"],
                    url=safe_url,
                    mode=active_mode,
                    mode_name=summary.get("mode_name", MODES[active_mode]["name"]),
                    summary=summary,
                )
                if hist_entry:
                    result_item["history_id"] = hist_entry.get("id")
                    summary["history_id"] = hist_entry.get("id")
                    try:
                        from vector_store import index_summary_entry
                        index_summary_entry(hist_entry["id"], api_key=api_key)
                    except Exception:
                        pass
            except Exception as e:
                logger.warning("Could not record history for %s: %s", safe_url, str(e))
        elif is_serverless_env():
            generated_id = str(uuid.uuid4())
            result_item["history_id"] = generated_id
            summary["history_id"] = generated_id

    except ScraperError as se:
        result_item["status"] = "failed"
        result_item["error"] = str(se)
    except SummarizerError as su:
        result_item["status"] = "failed"
        result_item["error"] = str(su)
    except Exception as e:
        result_item["status"] = "failed"
        result_item["error"] = f"Unexpected failure: {str(e)}"

    return result_item


def execute_batch(
    raw_urls: List[str],
    mode: str = DEFAULT_MODE,
    api_key: Optional[str] = None,
    custom_directive: Optional[str] = None,
    custom_directive_enabled: Optional[bool] = None,
    max_workers: int = DEFAULT_CONCURRENCY,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    batch_id: Optional[str] = None,
    provider_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Executes batch summarization across multiple URLs.
    Supports progress callbacks for streaming UI updates and deduplication.
    """
    validated_items, val_stats = validate_batch_urls(raw_urls)

    total_count = len(validated_items)
    results = [None] * total_count

    completed_count = 0
    failed_count = 0

    # Index map of unique valid URLs to process
    unique_tasks = {}  # normalized_url -> list of item indices
    for item in validated_items:
        idx = item["index"]
        if not item["is_valid"]:
            # Pre-failed due to validation or duplicate
            results[idx] = {
                "url": item["original_url"],
                "status": "failed",
                "error": item["error"] or "Invalid URL.",
                "mode": mode,
                "mode_name": MODES.get(mode, MODES[DEFAULT_MODE])["name"],
                "title": None,
                "summary": None,
                "history_id": None,
                "batch_id": batch_id,
            }
            failed_count += 1
            if progress_callback:
                progress_callback({
                    "event": "item_failed",
                    "index": idx,
                    "url": item["original_url"],
                    "item": results[idx],
                    "completed": completed_count,
                    "failed": failed_count,
                    "total": total_count,
                })
        else:
            norm_url = item["normalized_url"]
            if norm_url not in unique_tasks:
                unique_tasks[norm_url] = []
            unique_tasks[norm_url].append(idx)

    # Concurrency limit (bounded between 1 and 3 to prevent rate limits/exhaustion)
    workers = max(1, min(max_workers, 3))

    def _worker_task(target_url: str):
        return process_single_item(
            url=target_url,
            mode=mode,
            api_key=api_key,
            custom_directive=custom_directive,
            custom_directive_enabled=custom_directive_enabled,
            record_history=True,
            batch_id=batch_id,
            provider_config=provider_config,
        )

    # Process unique valid URLs with ThreadPoolExecutor
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_url = {
            executor.submit(_worker_task, url): url
            for url in unique_tasks.keys()
        }

        for future in concurrent.futures.as_completed(future_to_url):
            target_url = future_to_url[future]
            assigned_indices = unique_tasks[target_url]

            try:
                processed_result = future.result()
            except Exception as exc:
                processed_result = {
                    "url": target_url,
                    "status": "failed",
                    "error": f"Execution error: {str(exc)}",
                    "mode": mode,
                    "mode_name": MODES.get(mode, MODES[DEFAULT_MODE])["name"],
                    "title": None,
                    "summary": None,
                    "history_id": None,
                    "batch_id": batch_id,
                }

            is_success = (processed_result.get("status") == "completed")

            # Assign result to all indices that share this URL
            for idx in assigned_indices:
                results[idx] = dict(processed_result)
                if is_success:
                    completed_count += 1
                else:
                    failed_count += 1

                if progress_callback:
                    progress_callback({
                        "event": "item_completed" if is_success else "item_failed",
                        "index": idx,
                        "url": target_url,
                        "item": results[idx],
                        "completed": completed_count,
                        "failed": failed_count,
                        "total": total_count,
                    })

    return {
        "batch_id": batch_id,
        "mode": mode,
        "total": total_count,
        "completed": completed_count,
        "failed": failed_count,
        "results": results,
    }
