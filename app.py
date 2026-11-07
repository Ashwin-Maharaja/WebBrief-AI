import datetime
import json
import logging
import os
import re
import secrets
import urllib.parse
import uuid
import queue
import threading

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    redirect,
    url_for,
    session,
    send_file,
    Response,
    stream_with_context,
)

from scraper import scrape_article, ScraperError
from chunker import chunk_text
from summarizer import (
    summarize_text,
    summarize_single_chunk,
    synthesize_reduce_summary,
    SummarizerError,
    MODES,
    DEFAULT_MODE,
)
from job_manager import (
    init_job_tables,
    create_job,
    initialize_job_chunks,
    update_job_status,
    save_chunk_checkpoint,
    record_chunk_failure,
    get_job,
    get_job_chunks,
    get_job_events,
    list_jobs,
    delete_job,
    pause_job,
    cancel_job,
    recover_job,
    complete_job,
    check_config_consistency,
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
)
from byok import (
    has_valid_config,
    validate_cloudflare_credentials,
    validate_groq_credentials,
    get_active_provider_config,
    save_provider_config_to_env,
    clear_saved_provider_config,
    is_serverless_env,
    redact_credentials,
)
from services.providers.catalogue import (
    PROVIDER_CATALOGUE,
    get_models_for_provider,
    get_default_model,
)
from history import (
    add_history_entry,
    get_history,
    get_history_entry,
    delete_history_entry,
    clear_history,
    get_custom_directives,
    save_custom_directives,
    reset_custom_directives,
    MAX_DIRECTIVE_LENGTH,
)
from pdf_exporter import generate_summary_pdf, build_pdf_filename, generate_batch_summary_pdf, generate_comparison_pdf
from batch_processor import execute_batch, validate_batch_urls, MAX_BATCH_URLS
from comparative_analyzer import compare_articles, ComparisonError
from notion_service import (
    get_notion_key,
    has_notion_config,
    validate_notion_token,
    save_notion_key_to_env,
    clear_saved_notion_key,
    get_accessible_pages,
    create_notion_page,
    format_single_summary_blocks,
    format_comparison_blocks,
    NotionError,
)
from vector_store import (
    semantic_search,
    rebuild_vector_index,
    get_vector_store_stats,
    index_summary_entry,
    generate_embedding,
    VectorStoreError,
    DEFAULT_EMBEDDING_MODEL,
    EMBEDDING_DIMENSIONS,
    DEFAULT_SIMILARITY_THRESHOLD,
    DEFAULT_SEARCH_LIMIT,
)

logger = logging.getLogger(__name__)

APP_VERSION = "1.7.0"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)
handler = app

# Initialize resumable jobs database tables & recover stale runs (v1.7.0)
if not is_serverless_env():
    try:
        init_job_tables()
    except Exception as e:
        logger.debug("Job tables initialization deferred: %s", str(e))

# Request payload limit: enforce 2MB maximum
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_CONTENT_LENGTH", 2 * 1024 * 1024))

# Secret key for secure, signed session cookies (SEC-01)
flask_secret = os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY")
is_prod = is_serverless_env() or os.getenv("FLASK_ENV") == "production" or os.getenv("ENVIRONMENT") == "production"

if not flask_secret:
    deployment_seed = os.getenv("VERCEL_DEPLOYMENT_ID") or os.getenv("VERCEL_GIT_COMMIT_SHA")
    if deployment_seed:
        import hashlib
        flask_secret = hashlib.sha256(deployment_seed.encode("utf-8")).hexdigest()
        logger.info("FLASK_SECRET_KEY not set; derived deployment-specific secret key.")
    else:
        flask_secret = secrets.token_hex(32)
        logger.warning("No FLASK_SECRET_KEY configured. Generated ephemeral 32-byte secret key.")

app.secret_key = flask_secret

# Session Cookie Hardening (SEC-02)
is_secure_cookie = os.getenv("SESSION_COOKIE_SECURE", "true" if is_prod else "false").lower() in ("true", "1")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=is_secure_cookie,
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(hours=24),
)


@app.after_request
def apply_security_headers(response):
    """Inject standard defensive security headers into all HTTP responses (SEC-07)."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self';"
    )
    return response


@app.errorhandler(500)
def handle_internal_server_error(e):
    """Graceful handling of unhandled exceptions to prevent naked serverless crashes."""
    logger.error("Unhandled internal server error: %s", str(e), exc_info=True)
    if request.path.startswith("/api/") or request.is_json:
        return jsonify({"error": "Internal server error occurred.", "details": str(e)}), 500
    try:
        return render_template(
            "index.html",
            default_mode=DEFAULT_MODE,
            modes=MODES,
            is_configured=has_valid_config(session_dict=session),
            provider_config=get_active_provider_config(session_dict=session),
            provider_catalogue=PROVIDER_CATALOGUE,
            recent_history=[],
            error_message="An unexpected server error occurred. Please try again or check provider settings."
        ), 500
    except Exception:
        return "Internal Server Error", 500


@app.context_processor
def inject_globals():
    """Inject standard global template variables across all rendered views."""
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(32)

    return {
        "app_version": APP_VERSION,
        "default_mode": DEFAULT_MODE,
        "modes": MODES,
        "max_batch_urls": MAX_BATCH_URLS,
        "has_notion_configured": has_notion_config(session_dict=session),
        "is_hosted": is_serverless_env(),
        "csrf_token": session["_csrf_token"],
        "provider_catalogue": PROVIDER_CATALOGUE,
    }


@app.before_request
def validate_csrf_and_origin():
    """Enforces Origin and CSRF validation on all state-changing requests (SEC-03)."""
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        origin = request.headers.get("Origin")
        if origin:
            parsed_origin = urllib.parse.urlparse(origin)
            if parsed_origin.netloc and parsed_origin.netloc != request.host:
                return jsonify({"error": "Forbidden: Cross-origin request rejected."}), 403
        elif request.headers.get("Referer"):
            parsed_ref = urllib.parse.urlparse(request.headers.get("Referer"))
            if parsed_ref.netloc and parsed_ref.netloc != request.host:
                return jsonify({"error": "Forbidden: Invalid request origin."}), 403

        # Anti-CSRF token check for non-AJAX form submissions
        if not request.is_json and request.headers.get("X-Requested-With") != "XMLHttpRequest":
            submitted_token = request.form.get("csrf_token")
            expected_token = session.get("_csrf_token")
            if not submitted_token or not expected_token or not secrets.compare_digest(submitted_token, expected_token):
                if request.endpoint in ("clear_key", "save_key"):
                    return jsonify({"error": "Forbidden: Invalid or missing CSRF token."}), 403


@app.before_request
def enforce_byok_onboarding():
    """
    Enforces the multi-provider BYOK onboarding flow.
    If no valid AI provider credentials exist, redirects users to the dedicated /setup wizard.
    """
    exempt_endpoints = {
        "setup",
        "settings",
        "save_key",
        "save_config",
        "validate_key",
        "clear_key",
        "api_provider_models",
        "static",
        "view_history",
        "api_get_history",
        "api_get_history_entry",
        "api_delete_history",
        "api_clear_history",
        "export_pdf",
        "export_pdf_by_id",
        "api_get_preferences",
        "api_save_preferences",
        "api_reset_preferences",
        "batch_validate",
        "batch_export_pdf",
        "compare_export_pdf",
        "api_notion_status",
        "api_notion_connect",
        "api_notion_disconnect",
        "api_notion_pages",
        "api_notion_export",
        "api_search_semantic",
        "api_search_stats",
        "api_search_rebuild_index",
        "api_search_embed",
        "api_environment",
        "view_jobs",
        "api_list_jobs",
        "api_get_job_details",
        "api_delete_job",
        "api_pause_job",
        "api_cancel_job",
    }
    if request.endpoint in exempt_endpoints:
        return None

    if is_serverless_env() and request.endpoint in ("home",):
        return None

    # Check local configuration
    if has_valid_config(session_dict=session):
        return None

    # Handle per-request credentials in hosted mode
    if request.endpoint in (
        "summarize",
        "batch_summarize",
        "compare_articles_route",
        "api_create_job",
        "api_process_job_chunk",
        "api_synthesize_job",
        "api_run_job",
        "api_resume_job",
    ):
        payload = request.get_json() if request.is_json else request.form.to_dict()
        if has_valid_config(session_dict=session, request_data=payload):
            return None

        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({
                "error": "AI provider credentials not configured. Please complete setup wizard.",
                "redirect": url_for("setup")
            }), 401

        return redirect(url_for("setup"))

    return redirect(url_for("setup"))


@app.route("/")
def home():
    is_conf = has_valid_config(session_dict=session)
    recent_history = [] if is_serverless_env() else get_history(limit=10)
    provider_config = get_active_provider_config(session_dict=session)
    return render_template(
        "index.html",
        default_mode=DEFAULT_MODE,
        modes=MODES,
        is_configured=is_conf,
        provider_config=provider_config,
        provider_catalogue=PROVIDER_CATALOGUE,
        recent_history=recent_history
    )


@app.route("/setup")
def setup():
    """First-run setup wizard and provider configuration page."""
    mode = request.args.get("mode", "").strip().lower()
    is_conf = has_valid_config(session_dict=session)
    workspace_dir = os.path.dirname(os.path.abspath(__file__))
    env_exists = os.path.isfile(os.path.join(workspace_dir, ".env"))

    if is_conf and mode != "change":
        return redirect(url_for("settings"))

    provider_config = get_active_provider_config(session_dict=session)
    return render_template(
        "setup.html",
        is_configured=is_conf,
        env_exists=env_exists,
        mode=mode,
        provider_config=provider_config,
        provider_catalogue=PROVIDER_CATALOGUE,
    )


@app.route("/settings")
def settings():
    """Multi-provider settings, model selection, custom directives, and management page."""
    is_conf = has_valid_config(session_dict=session)
    is_serv = is_serverless_env()
    history_entries = [] if is_serv else get_history(limit=50)
    directives = get_custom_directives()
    provider_config = get_active_provider_config(session_dict=session)

    return render_template(
        "settings.html",
        is_configured=is_conf,
        is_serverless=is_serv,
        history_count=len(history_entries),
        modes=MODES,
        default_mode=DEFAULT_MODE,
        provider_config=provider_config,
        provider_catalogue=PROVIDER_CATALOGUE,
        custom_directive=directives.get("custom_directive", ""),
        custom_directive_enabled=directives.get("custom_directive_enabled", False),
        max_directive_length=MAX_DIRECTIVE_LENGTH,
    )


@app.route("/jobs")
def view_jobs():
    """Dedicated Summarization Jobs and Checkpoint Recovery view (v1.7.0)."""
    recent_jobs = [] if is_serverless_env() else list_jobs(limit=50)
    return render_template(
        "jobs.html",
        jobs=recent_jobs,
        is_hosted=is_serverless_env(),
        is_configured=has_valid_config(session),
        app_version=APP_VERSION,
    )


# -------------------------------------------------------------
# Resumable Jobs API Endpoints (v1.7.0)
# -------------------------------------------------------------
@app.route("/api/jobs", methods=["GET"])
def api_list_jobs():
    if is_serverless_env():
        return jsonify({"jobs": [], "is_hosted": True})
    jobs = list_jobs(limit=50)
    return jsonify({"jobs": jobs, "is_hosted": False})


@app.route("/api/jobs/<job_id>", methods=["GET"])
def api_get_job_details(job_id):
    if is_serverless_env():
        return jsonify({"error": "Job storage is client-side in hosted mode."}), 404
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404
    chunks = get_job_chunks(job_id)
    events = get_job_events(job_id)
    return jsonify({"job": job, "chunks": chunks, "events": events})


@app.route("/api/jobs/create", methods=["POST"])
def api_create_job():
    data = request.get_json() or {} if request.is_json else request.form.to_dict()
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "Please provide a valid URL."}), 400

    mode = data.get("mode", DEFAULT_MODE).strip().lower()
    if mode not in MODES:
        mode = DEFAULT_MODE

    try:
        article_text = scrape_article(url)
        chunks = chunk_text(article_text)
        total_chunks = len(chunks)
        title = url

        if is_serverless_env():
            job_id = str(uuid.uuid4())
            chunk_items = [
                {"chunk_index": idx, "chunk_text": ch, "status": "pending"}
                for idx, ch in enumerate(chunks, 1)
            ]
            return jsonify({
                "success": True,
                "job_id": job_id,
                "source_url": url,
                "title": title,
                "mode": mode,
                "total_chunks": total_chunks,
                "chunks": chunk_items,
                "article_text": article_text,
            })

        job_id = create_job(source_url=url, mode=mode, provider_config=data, title=title, article_text=article_text)
        initialize_job_chunks(job_id, chunks, title=title, article_text=article_text)
        chunks_meta = [
            {"chunk_index": idx, "chunk_text": ch, "status": "pending"}
            for idx, ch in enumerate(chunks, 1)
        ]
        return jsonify({
            "success": True,
            "job_id": job_id,
            "source_url": url,
            "title": title,
            "mode": mode,
            "total_chunks": total_chunks,
            "chunks": chunks_meta,
        })
    except Exception as e:
        return jsonify({"error": redact_credentials(f"Failed to create job: {str(e)}")}), 500


@app.route("/api/jobs/<job_id>/chunk/<int:chunk_index>", methods=["POST"])
def api_process_job_chunk(job_id, chunk_index):
    data = request.get_json() or {}
    chunk_text_input = data.get("chunk_text")
    total_chunks = int(data.get("total_chunks", 1))
    mode = data.get("mode", DEFAULT_MODE)

    if not is_serverless_env():
        chunks = get_job_chunks(job_id)
        target_chunk = next((c for c in chunks if c["chunk_index"] == chunk_index), None)
        if target_chunk:
            chunk_text_input = target_chunk["chunk_text"]
            total_chunks = len(chunks)

    if not chunk_text_input:
        return jsonify({"error": f"Chunk {chunk_index} content not found."}), 400

    try:
        content, map_res = summarize_single_chunk(
            chunk_content=chunk_text_input,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            mode=mode,
            provider_config=data,
        )

        if not is_serverless_env():
            save_chunk_checkpoint(
                job_id=job_id,
                chunk_index=chunk_index,
                summary_output=content,
                provider=map_res.provider_id,
                model=map_res.model,
            )

        return jsonify({
            "success": True,
            "job_id": job_id,
            "chunk_index": chunk_index,
            "summary_output": content,
            "provider": map_res.provider_id,
            "provider_name": map_res.provider_name,
            "model": map_res.model,
            "switched_to_fallback": map_res.switched_to_fallback,
        })
    except Exception as e:
        if not is_serverless_env():
            record_chunk_failure(job_id, chunk_index, str(e))
        return jsonify({"error": redact_credentials(str(e))}), 500


@app.route("/api/jobs/<job_id>/synthesize", methods=["POST"])
def api_synthesize_job(job_id):
    data = request.get_json() or {}
    intermediate_summaries = data.get("intermediate_summaries")
    total_chunks = data.get("total_chunks")
    mode = data.get("mode", DEFAULT_MODE)
    title = data.get("title", "Webpage Summary")
    url = data.get("url", "")

    if not is_serverless_env():
        job = get_job(job_id)
        if not job:
            return jsonify({"error": "Job not found."}), 404
        mode = job.get("mode", mode)
        title = job.get("title", title)
        url = job.get("source_url", url)

        chunks = get_job_chunks(job_id)
        total_chunks = len(chunks)
        completed = [c for c in chunks if c["status"] == "completed" and c.get("summary_output")]
        intermediate_summaries = [(c["chunk_index"], c["summary_output"]) for c in completed]

    if not intermediate_summaries:
        return jsonify({"error": "No intermediate summaries available to synthesize."}), 400

    try:
        if not is_serverless_env():
            update_job_status(job_id, STATUS_SYNTHESIZING)

        final_summary = synthesize_reduce_summary(
            intermediate_summaries=intermediate_summaries,
            total_chunks=total_chunks or len(intermediate_summaries),
            mode=mode,
            provider_config=data,
        )

        history_id = None
        if is_serverless_env():
            history_id = str(uuid.uuid4())
        else:
            hist_entry = add_history_entry(
                title=final_summary.get("title") or title,
                url=url,
                mode=mode,
                mode_name=final_summary.get("mode_name", mode.capitalize()),
                summary=final_summary,
            )
            if hist_entry:
                history_id = hist_entry.get("id")
            complete_job(job_id, final_summary, history_id)

        return jsonify({
            "success": True,
            "job_id": job_id,
            "summary": final_summary,
            "history_id": history_id,
        })
    except Exception as e:
        return jsonify({"error": redact_credentials(f"Final synthesis failed: {str(e)}")}), 500


@app.route("/api/jobs/<job_id>/resume", methods=["POST"])
def api_resume_job(job_id):
    if is_serverless_env():
        return jsonify({"error": "Use client-side orchestration in hosted mode."}), 400

    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404

    data = request.get_json() or {}
    consistency = check_config_consistency(job_id, current_config=data, current_mode=data.get("mode"))
    force_override = data.get("force_override")

    if not consistency["consistent"] and not force_override:
        return jsonify({
            "success": False,
            "requires_confirmation": True,
            "differences": consistency["differences"],
            "original_config": consistency["original_config"],
            "message": "Configuration differs from when the job started. Choose to resume with original settings or apply current settings."
        }), 409

    rec_info = recover_job(job_id)
    return jsonify({
        "success": True,
        "recovery_info": rec_info,
    })


@app.route("/api/jobs/<job_id>/run", methods=["POST"])
def api_run_job(job_id):
    if is_serverless_env():
        return jsonify({"error": "Server-side run loop disabled in hosted mode."}), 400

    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404

    if job["status"] == STATUS_CANCELLED:
        return jsonify({"error": "Cancelled jobs cannot be resumed."}), 400

    data = request.get_json() or {}
    recover_job(job_id)
    chunks = get_job_chunks(job_id)
    total_chunks = len(chunks)

    try:
        for c in chunks:
            if c["status"] == "completed" and c.get("summary_output"):
                continue

            latest = get_job(job_id)
            if latest and latest["status"] in (STATUS_PAUSED, STATUS_CANCELLED):
                return jsonify({"success": False, "status": latest["status"], "message": f"Job {latest['status'].lower()}."})

            idx = c["chunk_index"]
            content, map_res = summarize_single_chunk(
                chunk_content=c["chunk_text"],
                chunk_index=idx,
                total_chunks=total_chunks,
                mode=job["mode"],
                provider_config=data,
            )
            save_chunk_checkpoint(
                job_id=job_id,
                chunk_index=idx,
                summary_output=content,
                provider=map_res.provider_id,
                model=map_res.model,
            )

        completed_chunks = get_job_chunks(job_id)
        intermediates = [(c["chunk_index"], c["summary_output"]) for c in completed_chunks if c["status"] == "completed"]
        update_job_status(job_id, STATUS_SYNTHESIZING)
        final_summary = synthesize_reduce_summary(
            intermediate_summaries=intermediates,
            total_chunks=total_chunks,
            mode=job["mode"],
            provider_config=data,
        )
        hist_entry = add_history_entry(
            title=final_summary.get("title") or job.get("title"),
            url=job.get("source_url"),
            mode=job["mode"],
            mode_name=final_summary.get("mode_name", job["mode"].capitalize()),
            summary=final_summary,
        )
        history_id = hist_entry.get("id") if hist_entry else None
        complete_job(job_id, final_summary, history_id)

        return jsonify({
            "success": True,
            "status": STATUS_COMPLETED,
            "summary": final_summary,
            "history_id": history_id,
        })
    except Exception as e:
        return jsonify({"error": redact_credentials(str(e))}), 500


@app.route("/api/jobs/<job_id>/pause", methods=["POST"])
def api_pause_job(job_id):
    if is_serverless_env():
        return jsonify({"success": True})
    success = pause_job(job_id)
    return jsonify({"success": success})


@app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
def api_cancel_job(job_id):
    if is_serverless_env():
        return jsonify({"success": True})
    success = cancel_job(job_id)
    return jsonify({"success": success})


@app.route("/api/jobs/<job_id>", methods=["DELETE"])
def api_delete_job(job_id):
    if is_serverless_env():
        return jsonify({"success": True})
    success = delete_job(job_id)
    return jsonify({"success": success})


@app.route("/history")
def view_history():
    """Dedicated summary history view."""
    entries = [] if is_serverless_env() else get_history(limit=50)
    return render_template("history.html", entries=entries)


# -------------------------------------------------------------
# Summary History API Endpoints
# -------------------------------------------------------------
@app.route("/api/history", methods=["GET"])
def api_get_history():
    if is_serverless_env():
        return jsonify({"history": []})
    entries = get_history(limit=50)
    return jsonify({"history": entries})


@app.route("/api/history/<entry_id>", methods=["GET"])
def api_get_history_entry(entry_id):
    if is_serverless_env():
        return jsonify({"error": "History entry not found."}), 404
    entry = get_history_entry(entry_id)
    if not entry:
        return jsonify({"error": "History entry not found."}), 404
    return jsonify({"entry": entry})


@app.route("/api/history/<entry_id>", methods=["DELETE"])
def api_delete_history(entry_id):
    if is_serverless_env():
        return jsonify({"success": True})
    success = delete_history_entry(entry_id)
    if not success:
        return jsonify({"error": "Failed to delete history entry."}), 404
    return jsonify({"success": True})


@app.route("/api/history/clear", methods=["POST"])
def api_clear_history():
    if is_serverless_env():
        return jsonify({"success": True})
    clear_history()
    return jsonify({"success": True})


# -------------------------------------------------------------
# PDF Document Export Endpoints
# -------------------------------------------------------------
@app.route("/export-pdf", methods=["POST"])
def export_pdf():
    data = request.get_json() if request.is_json else request.form.to_dict()
    history_id = data.get("history_id")
    summary = None
    url = data.get("url", "")
    title = data.get("title", "")
    mode = data.get("mode", "")
    created_at = data.get("created_at", "")

    if history_id and not is_serverless_env():
        entry = get_history_entry(history_id)
        if entry:
            summary = entry.get("summary")
            url = entry.get("url", url)
            title = entry.get("title", title)
            mode = entry.get("mode_name", mode)
            created_at = entry.get("created_at_formatted", created_at)

    if not summary:
        raw_summary = data.get("summary")
        if isinstance(raw_summary, str):
            try:
                summary = json.loads(raw_summary)
            except Exception:
                summary = {"overview": raw_summary}
        elif isinstance(raw_summary, dict):
            summary = raw_summary

    if not summary or not isinstance(summary, dict):
        return jsonify({"error": "No valid summary data provided for PDF generation."}), 400

    try:
        pdf_buffer = generate_summary_pdf(
            summary=summary,
            url=url,
            title=title or summary.get("title"),
            mode=mode or summary.get("mode_name"),
            created_at=created_at,
        )
        download_name = build_pdf_filename(title or summary.get("title"))

        return send_file(
            pdf_buffer,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=download_name,
        )
    except Exception as e:
        return jsonify({"error": redact_credentials(f"Failed to generate PDF: {str(e)}")}), 500


@app.route("/export-pdf/<entry_id>", methods=["GET"])
def export_pdf_by_id(entry_id):
    if is_serverless_env():
        return jsonify({"error": "History entry not found."}), 404

    entry = get_history_entry(entry_id)
    if not entry:
        return jsonify({"error": "History entry not found."}), 404

    try:
        summary_data = entry.get("summary", {})
        pdf_buffer = generate_summary_pdf(
            summary=summary_data,
            url=entry.get("url", ""),
            title=entry.get("title", ""),
            mode=entry.get("mode_name", ""),
            created_at=entry.get("created_at_formatted", ""),
        )
        download_name = build_pdf_filename(entry.get("title"))

        return send_file(
            pdf_buffer,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=download_name,
        )
    except Exception as e:
        return jsonify({"error": redact_credentials(f"Failed to generate PDF: {str(e)}")}), 500


# -------------------------------------------------------------
# Custom Prompt Directives API Endpoints
# -------------------------------------------------------------
@app.route("/api/preferences", methods=["GET"])
def api_get_preferences():
    return jsonify(get_custom_directives())


@app.route("/api/preferences", methods=["POST"])
def api_save_preferences():
    data = request.get_json() if request.is_json else request.form
    directive = (data.get("custom_directive") or "").strip()
    enabled = data.get("custom_directive_enabled") in (True, "true", "1", 1, "on")

    success, message = save_custom_directives(directive=directive, enabled=enabled)
    status_code = 200 if success else 400
    return jsonify({
        "success": success,
        "message": message,
        "preferences": get_custom_directives()
    }), status_code


@app.route("/api/preferences/reset", methods=["POST"])
def api_reset_preferences():
    reset_custom_directives()
    return jsonify({
        "success": True,
        "message": "Custom prompt directives have been reset to default.",
        "preferences": get_custom_directives()
    })


# -------------------------------------------------------------
# Batch Summarization Endpoints
# -------------------------------------------------------------
@app.route("/batch/validate", methods=["POST"])
def batch_validate():
    data = request.get_json() or {}
    raw_urls = data.get("urls", [])
    if not isinstance(raw_urls, list):
        return jsonify({"error": "Expected an array of URLs."}), 400

    items, stats = validate_batch_urls(raw_urls)
    return jsonify({"items": items, "stats": stats})


@app.route("/batch/summarize", methods=["POST"])
def batch_summarize():
    data = request.get_json() or {}
    raw_urls = data.get("urls", [])

    if not raw_urls or not isinstance(raw_urls, list):
        return jsonify({"error": "Please provide a list of URLs to summarize."}), 400

    cleaned_urls = [str(u).strip() for u in raw_urls if str(u).strip()]
    if not cleaned_urls:
        return jsonify({"error": "Please provide at least one valid URL."}), 400

    if len(cleaned_urls) > MAX_BATCH_URLS:
        cleaned_urls = cleaned_urls[:MAX_BATCH_URLS]

    mode = data.get("mode", DEFAULT_MODE)
    api_key = data.get("api_key")
    custom_directive = data.get("custom_directive")
    custom_directive_enabled = data.get("custom_directive_enabled")

    is_stream = request.args.get("stream") in ("1", "true") or request.headers.get("Accept") == "text/event-stream"

    if is_stream:
        event_queue = queue.Queue()

        def stream_progress_cb(evt):
            event_queue.put(evt)

        def generate_sse():
            batch_result_container = []

            def worker_thread():
                try:
                    res = execute_batch(
                        raw_urls=cleaned_urls,
                        mode=mode,
                        api_key=api_key,
                        custom_directive=custom_directive,
                        custom_directive_enabled=custom_directive_enabled,
                        progress_callback=stream_progress_cb,
                        provider_config=data,
                    )
                    batch_result_container.append(res)
                except Exception as e:
                    event_queue.put({"event": "error", "error": redact_credentials(str(e))})
                finally:
                    event_queue.put(None)

            t = threading.Thread(target=worker_thread)
            t.start()

            while True:
                try:
                    evt = event_queue.get(timeout=1.0)
                    if evt is None:
                        break
                    yield f"data: {json.dumps(evt)}\n\n"
                except queue.Empty:
                    if not t.is_alive():
                        break
                    yield f"data: {json.dumps({'event': 'heartbeat'})}\n\n"

            t.join(timeout=5.0)
            final_res = batch_result_container[0] if batch_result_container else {}
            yield f"data: {json.dumps({'event': 'done', 'result': final_res})}\n\n"

        return Response(stream_with_context(generate_sse()), mimetype="text/event-stream")

    # Standard JSON batch execution
    batch_result = execute_batch(
        raw_urls=cleaned_urls,
        mode=mode,
        api_key=api_key,
        custom_directive=custom_directive,
        custom_directive_enabled=custom_directive_enabled,
        provider_config=data,
    )
    batch_result["success"] = True
    return jsonify(batch_result)


@app.route("/batch/export-pdf", methods=["POST"])
def batch_export_pdf():
    data = request.get_json() or {}
    items = data.get("items", [])

    history_ids = data.get("history_ids")
    if history_ids and isinstance(history_ids, list):
        items = []
        for hid in history_ids:
            hentry = get_history_entry(hid)
            if hentry:
                items.append(hentry)

    if not items or not isinstance(items, list):
        return jsonify({"error": "No items provided for batch PDF export."}), 400

    try:
        pdf_buf = generate_batch_summary_pdf(items)
        date_str = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        download_name = f"WebBrief_Batch_Report_{date_str}.pdf"
        return send_file(
            pdf_buf,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=download_name,
        )
    except Exception as e:
        return jsonify({"error": redact_credentials(f"Failed to generate batch PDF: {str(e)}")}), 500


# -------------------------------------------------------------
# Comparative Research Endpoints (v1.3)
# -------------------------------------------------------------
@app.route("/compare", methods=["POST"])
def compare_articles_route():
    data = request.get_json() if request.is_json else request.form.to_dict()
    url_a = data.get("url_a", "").strip()
    url_b = data.get("url_b", "").strip()

    if not url_a or not url_b:
        return jsonify({"error": "Please provide both Article A and Article B URLs for comparison."}), 400

    if not has_valid_config(session_dict=session, request_data=data):
        return jsonify({
            "error": "AI provider credentials not configured. Please complete setup wizard.",
            "redirect": url_for("setup")
        }), 401

    directives = get_custom_directives()
    custom_directive = directives.get("custom_directive")
    custom_directive_enabled = directives.get("custom_directive_enabled")
    if "custom_directive" in data:
        custom_directive = data.get("custom_directive")
    if "custom_directive_enabled" in data:
        custom_directive_enabled = bool(data.get("custom_directive_enabled"))

    try:
        comparison_res = compare_articles(
            url_a=url_a,
            url_b=url_b,
            custom_directive=custom_directive,
            custom_directive_enabled=custom_directive_enabled,
            provider_config=data,
        )

        history_id = None
        if is_serverless_env():
            history_id = str(uuid.uuid4())
            comparison_res["history_id"] = history_id
        else:
            try:
                hist_entry = add_history_entry(
                    title=comparison_res.get("title", "Comparative Analysis"),
                    url=url_a,
                    mode="comparison",
                    mode_name="Comparative Synthesis",
                    summary=comparison_res,
                    entry_type="comparison",
                    url_b=url_b,
                    title_b=comparison_res.get("article_b", {}).get("title", "Article B"),
                )
                if hist_entry:
                    history_id = hist_entry.get("id")
                    comparison_res["history_id"] = history_id
                    try:
                        index_summary_entry(history_id, provider_config=data)
                    except Exception:
                        pass
            except Exception as e:
                logger.warning("Could not record comparison to history: %s", str(e))

        return jsonify({
            "success": True,
            "comparison": comparison_res,
            "history_id": history_id,
            "url_a": url_a,
            "url_b": url_b,
        })

    except ComparisonError as ce:
        return jsonify({"error": redact_credentials(str(ce))}), 400
    except Exception as e:
        return jsonify({"error": redact_credentials(f"Comparative research failed: {str(e)}")}), 500


@app.route("/compare/export-pdf", methods=["POST"])
def compare_export_pdf():
    data = request.get_json() if request.is_json else request.form.to_dict()
    comparison_data = data.get("comparison")

    if isinstance(comparison_data, str):
        try:
            comparison_data = json.loads(comparison_data)
        except Exception:
            comparison_data = None

    if not comparison_data or not isinstance(comparison_data, dict):
        history_id = data.get("history_id")
        if history_id and not is_serverless_env():
            hentry = get_history_entry(history_id)
            if hentry:
                comparison_data = hentry.get("summary")

    if not comparison_data or not isinstance(comparison_data, dict):
        return jsonify({"error": "No valid comparative analysis data provided for PDF export."}), 400

    try:
        url_a = data.get("url_a", comparison_data.get("article_a", {}).get("url", ""))
        url_b = data.get("url_b", comparison_data.get("article_b", {}).get("url", ""))
        created_at = data.get("created_at")

        pdf_buf = generate_comparison_pdf(
            comparison=comparison_data,
            url_a=url_a,
            url_b=url_b,
            created_at=created_at,
        )

        title = comparison_data.get("title", "Comparative_Research")
        safe_title = re.sub(r"[^\w\s-]", "", title).strip()[:40].replace(" ", "_")
        date_str = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        download_name = f"WebBrief_Comparison_{safe_title}_{date_str}.pdf"

        return send_file(
            pdf_buf,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=download_name,
        )
    except Exception as e:
        return jsonify({"error": redact_credentials(f"Failed to generate comparative PDF: {str(e)}")}), 500


# -------------------------------------------------------------
# Notion Integration Endpoints
# -------------------------------------------------------------
@app.route("/api/notion/status", methods=["GET"])
def api_notion_status():
    configured = has_notion_config(session_dict=session)
    return jsonify({
        "configured": configured,
        "is_hosted": is_serverless_env(),
    })


@app.route("/api/notion/connect", methods=["POST"])
def api_notion_connect():
    data = request.get_json() or {}
    token = data.get("token", "").strip()
    if not token:
        return jsonify({"error": "Please provide an internal Notion integration token."}), 400

    is_valid, msg = validate_notion_token(token)
    if not is_valid:
        return jsonify({"error": msg}), 400

    success, save_msg = save_notion_key_to_env(token, session_dict=session)
    if not success:
        return jsonify({"error": save_msg}), 500

    pages = get_accessible_pages(api_key=token)
    return jsonify({
        "success": True,
        "message": "Notion connected successfully.",
        "pages": pages,
    })


@app.route("/api/notion/disconnect", methods=["POST"])
def api_notion_disconnect():
    clear_saved_notion_key(session_dict=session)
    return jsonify({"success": True, "message": "Notion disconnected."})


@app.route("/api/notion/pages", methods=["GET"])
def api_notion_pages():
    token = get_notion_key(session_dict=session)
    if not token:
        return jsonify({"error": "Notion is not connected."}), 401
    pages = get_accessible_pages(api_key=token)
    return jsonify({"pages": pages})


@app.route("/api/notion/export", methods=["POST"])
def api_notion_export():
    token = get_notion_key(session_dict=session)
    data = request.get_json() or {}
    if not token:
        token = data.get("notion_token", "").strip()
    if not token:
        return jsonify({"error": "Notion is not connected. Please connect in Settings."}), 401

    parent_page_id = data.get("parent_page_id", "").strip()
    if not parent_page_id:
        return jsonify({"error": "Please select a target Notion page."}), 400

    entry_type = data.get("entry_type", "single")
    summary_data = data.get("summary")

    if not summary_data:
        history_id = data.get("history_id")
        if history_id and not is_serverless_env():
            entry = get_history_entry(history_id)
            if entry:
                summary_data = entry.get("summary")
                entry_type = entry.get("entry_type", entry_type)

    if not summary_data or not isinstance(summary_data, dict):
        return jsonify({"error": "No summary data found to export."}), 400

    try:
        if entry_type == "comparison":
            title = summary_data.get("title") or "WebBrief Comparative Analysis"
            blocks = format_comparison_blocks(
                summary_data,
                url_a=data.get("url_a", ""),
                url_b=data.get("url_b", ""),
            )
        else:
            title = summary_data.get("title") or "Webpage Summary"
            blocks = format_single_summary_blocks(
                summary_data,
                url=data.get("url", ""),
                mode_name=data.get("mode_name", summary_data.get("mode_name", "Summary")),
            )

        res = create_notion_page(
            parent_page_id=parent_page_id,
            title=title,
            blocks=blocks,
            api_key=token,
        )
        return jsonify({"success": True, "page_url": res["url"], "title": title})

    except NotionError as ne:
        return jsonify({"error": redact_credentials(str(ne))}), 400
    except Exception as e:
        return jsonify({"error": redact_credentials(f"Failed to export to Notion: {str(e)}")}), 500


# -------------------------------------------------------------
# Local Knowledge Retrieval & Semantic Search Endpoints (v1.4 / v1.6)
# -------------------------------------------------------------
@app.route("/api/search/semantic", methods=["POST"])
def api_search_semantic():
    if is_serverless_env():
        return jsonify({
            "error": "Server-side semantic search is disabled in hosted mode. Please use client-side search via /api/search/embed."
        }), 400

    data = request.get_json() or {}
    query = data.get("query", "").strip()

    if not query:
        return jsonify({"error": "Please enter a search query."}), 400

    if not has_valid_config(session_dict=session, request_data=data):
        return jsonify({
            "error": "AI provider credentials required to embed search queries. Please configure your credentials in Settings.",
            "redirect": url_for("setup")
        }), 401

    try:
        top_k = int(data.get("top_k", DEFAULT_SEARCH_LIMIT))
        if top_k < 1 or top_k > 25:
            top_k = DEFAULT_SEARCH_LIMIT
    except (ValueError, TypeError):
        top_k = DEFAULT_SEARCH_LIMIT

    try:
        threshold = float(data.get("threshold", DEFAULT_SIMILARITY_THRESHOLD))
        if threshold < 0.0 or threshold > 1.0:
            threshold = DEFAULT_SIMILARITY_THRESHOLD
    except (ValueError, TypeError):
        threshold = DEFAULT_SIMILARITY_THRESHOLD

    filter_type = data.get("filter_type")

    try:
        results = semantic_search(
            query=query,
            top_k=top_k,
            threshold=threshold,
            filter_type=filter_type,
            provider_config=data,
        )
        return jsonify({
            "success": True,
            "query": query,
            "count": len(results),
            "threshold": threshold,
            "results": results,
        })
    except VectorStoreError as ve:
        return jsonify({"error": redact_credentials(str(ve))}), 400
    except Exception as e:
        return jsonify({"error": redact_credentials(f"Semantic search failed: {str(e)}")}), 500


@app.route("/api/search/stats", methods=["GET"])
def api_search_stats():
    if is_serverless_env():
        return jsonify({"indexed_count": 0, "total_summaries": 0, "status": "hosted_indexeddb"})
    stats = get_vector_store_stats()
    return jsonify(stats)


@app.route("/api/search/rebuild-index", methods=["POST"])
def api_search_rebuild_index():
    if is_serverless_env():
        return jsonify({"error": "Vector indexing on server is disabled in hosted mode."}), 400

    data = request.get_json() or {}
    if not has_valid_config(session_dict=session, request_data=data):
        return jsonify({
            "error": "AI provider credentials required to generate embeddings. Please configure your credentials in Settings.",
            "redirect": url_for("setup")
        }), 401

    try:
        result = rebuild_vector_index(provider_config=data)
        return jsonify({
            "success": True,
            "stats": result,
            "message": f"Successfully indexed {result.get('indexed', 0)} of {result.get('total', 0)} summaries."
        })
    except Exception as e:
        return jsonify({"error": redact_credentials(f"Failed to rebuild index: {str(e)}")}), 500


@app.route("/api/search/embed", methods=["POST"])
def api_search_embed():
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "No text provided to embed."}), 400

    if not has_valid_config(session_dict=session, request_data=data):
        return jsonify({"error": "AI provider credentials required."}), 401

    try:
        vector = generate_embedding(text, provider_config=data)
        return jsonify({
            "embedding": vector,
            "model": DEFAULT_EMBEDDING_MODEL,
            "dimensions": len(vector),
        })
    except Exception as e:
        return jsonify({"error": redact_credentials(f"Embedding generation failed: {str(e)}")}), 500


@app.route("/api/environment", methods=["GET"])
def api_environment():
    hosted = is_serverless_env()
    return jsonify({
        "version": APP_VERSION,
        "is_hosted": hosted,
        "environment": "hosted_serverless" if hosted else "local_self_hosted",
        "ssrf_guard": "active"
    })


# -------------------------------------------------------------
# Multi-Provider BYOK Configuration Endpoints (v1.6.0)
# -------------------------------------------------------------
@app.route("/api/providers/models", methods=["GET"])
def api_provider_models():
    """Returns verified model catalogue for supported providers."""
    provider_id = request.args.get("provider", "").strip().lower()
    if provider_id:
        return jsonify({
            "provider": provider_id,
            "models": get_models_for_provider(provider_id),
            "default_model": get_default_model(provider_id),
        })
    return jsonify(PROVIDER_CATALOGUE)


@app.route("/api/validate-key", methods=["POST"])
def validate_key():
    """
    Validates submitted provider credentials without consuming generation tokens.
    Supports Cloudflare Workers AI and GroqCloud.
    """
    data = request.get_json() or {} if request.is_json else request.form.to_dict()
    provider = data.get("provider", "").strip().lower()

    # Detect provider if not explicitly given
    if not provider:
        if data.get("cloudflare_account_id") or data.get("cloudflare_api_token") or data.get("account_id") or data.get("api_token"):
            provider = "cloudflare"
        elif data.get("groq_api_key") or (data.get("api_key", "").startswith("gsk_")):
            provider = "groq"
        else:
            provider = "cloudflare"

    if provider == "cloudflare":
        account_id = data.get("cloudflare_account_id") or data.get("account_id", "").strip()
        api_token = data.get("cloudflare_api_token") or data.get("api_token", "").strip()
        is_valid, message = validate_cloudflare_credentials(account_id, api_token)
    elif provider == "groq":
        api_key = data.get("groq_api_key") or data.get("api_key", "").strip()
        is_valid, message = validate_groq_credentials(api_key)
    else:
        return jsonify({"valid": False, "message": f"Unsupported provider: '{provider}'"}), 400

    status_code = 200 if is_valid else 400
    return jsonify({"valid": is_valid, "message": message}), status_code


@app.route("/api/save-key", methods=["POST"])
@app.route("/api/save-config", methods=["POST"])
def save_key():
    """
    Validates and persists provider configuration.
    In local self-hosted mode: saves to .env.
    In hosted serverless mode: verifies credentials and confirms active session.
    """
    if request.is_json:
        data = request.get_json() or {}
        is_ajax = True
    else:
        data = request.form.to_dict()
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    primary_provider = data.get("primary_provider", "cloudflare").strip().lower()
    confirm_overwrite = data.get("confirm_overwrite") in (True, "true", "1", 1, "on")

    # Step 1: Validate primary provider credentials
    if primary_provider == "cloudflare":
        cf_id = data.get("cloudflare_account_id", "").strip()
        cf_token = data.get("cloudflare_api_token", "").strip()
        is_valid, val_msg = validate_cloudflare_credentials(cf_id, cf_token)
    elif primary_provider == "groq":
        groq_key = data.get("groq_api_key", "").strip() or data.get("api_key", "").strip()
        is_valid, val_msg = validate_groq_credentials(groq_key)
    else:
        is_valid, val_msg = False, f"Unknown primary provider: '{primary_provider}'"

    if not is_valid:
        if is_ajax:
            return jsonify({"success": False, "error": val_msg}), 400
        workspace_dir = os.path.dirname(os.path.abspath(__file__))
        env_exists = os.path.isfile(os.path.join(workspace_dir, ".env"))
        return render_template("setup.html", error=val_msg, is_configured=has_valid_config(session), env_exists=env_exists), 400

    # Step 2: Validate fallback provider credentials if fallback is enabled
    fallback_enabled = data.get("fallback_enabled") in (True, "true", "1", 1, "on")
    if fallback_enabled:
        fallback_provider = data.get("fallback_provider", "").strip().lower()
        if fallback_provider == primary_provider:
            err = "Fallback provider cannot be identical to the primary provider."
            if is_ajax:
                return jsonify({"success": False, "error": err}), 400
            return render_template("setup.html", error=err, is_configured=has_valid_config(session)), 400

        if fallback_provider == "cloudflare":
            fb_id = data.get("cloudflare_account_id", "").strip()
            fb_token = data.get("cloudflare_api_token", "").strip()
            fb_valid, fb_msg = validate_cloudflare_credentials(fb_id, fb_token)
            if not fb_valid:
                if is_ajax:
                    return jsonify({"success": False, "error": f"Fallback provider error: {fb_msg}"}), 400
                return render_template("setup.html", error=f"Fallback provider error: {fb_msg}", is_configured=has_valid_config(session)), 400
        elif fallback_provider == "groq":
            fb_key = data.get("groq_api_key", "").strip()
            fb_valid, fb_msg = validate_groq_credentials(fb_key)
            if not fb_valid:
                if is_ajax:
                    return jsonify({"success": False, "error": f"Fallback provider error: {fb_msg}"}), 400
                return render_template("setup.html", error=f"Fallback provider error: {fb_msg}", is_configured=has_valid_config(session)), 400

    # Hosted serverless mode: do not touch filesystem
    if is_serverless_env():
        if is_ajax:
            return jsonify({
                "success": True,
                "message": "Provider configuration verified for active browser session.",
                "is_hosted": True,
                "redirect": url_for("home")
            })
        return redirect(url_for("home"))

    # Local mode: persist to .env
    success, save_message = save_provider_config_to_env(
        config_dict=data,
        confirm_overwrite=confirm_overwrite
    )

    if not success:
        if is_ajax:
            return jsonify({"success": False, "error": save_message}), 400
        workspace_dir = os.path.dirname(os.path.abspath(__file__))
        env_exists = os.path.isfile(os.path.join(workspace_dir, ".env"))
        return render_template("setup.html", error=save_message, is_configured=has_valid_config(session), env_exists=env_exists), 400

    if is_ajax:
        return jsonify({"success": True, "message": save_message, "redirect": url_for("home")})

    return redirect(url_for("home"))


@app.route("/settings/clear-key", methods=["POST"])
def clear_key():
    """Removes stored provider credentials and routing configuration."""
    clear_saved_provider_config()
    return redirect(url_for("setup"))


# -------------------------------------------------------------
# Webpage Summarization Endpoint
# -------------------------------------------------------------
@app.route("/summarize", methods=["POST"])
def summarize():
    if request.is_json:
        data = request.get_json() or {}
        url = data.get("url", "").strip()
        mode = data.get("mode", DEFAULT_MODE).strip().lower()
        custom_directive = data.get("custom_directive")
        custom_directive_enabled = data.get("custom_directive_enabled")
        is_ajax = True
    else:
        data = request.form.to_dict()
        url = request.form.get("url", "").strip()
        mode = request.form.get("mode", DEFAULT_MODE).strip().lower()
        custom_directive = request.form.get("custom_directive")
        custom_directive_enabled = request.form.get("custom_directive_enabled")
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if mode not in MODES:
        mode = DEFAULT_MODE

    if not url:
        error_msg = "Please enter a webpage URL."
        if is_ajax:
            return jsonify({"error": error_msg}), 400
        return render_template("index.html", error=error_msg, url="", mode=mode, modes=MODES, is_configured=has_valid_config(session)), 400

    if not has_valid_config(session_dict=session, request_data=data):
        error_msg = "AI provider credentials not configured. Please complete setup wizard."
        if is_ajax:
            return jsonify({"error": error_msg, "redirect": url_for("setup")}), 401
        return render_template("index.html", error=error_msg, url=url, mode=mode, modes=MODES, is_configured=False), 401

    try:
        # Step 1: Scrape article text from webpage
        article_text = scrape_article(url)

        # Step 2: Initialize resumable job tracking (v1.7.0)
        job_id = data.get("job_id")
        if not job_id and not is_serverless_env():
            try:
                job_id = create_job(
                    source_url=url,
                    mode=mode,
                    provider_config=data,
                    article_text=article_text
                )
            except Exception as e:
                logger.debug("Automatic job tracking initialization deferred: %s", str(e))

        # Step 3: Generate AI summary with multi-provider routing & checkpoint persistence
        summarize_kwargs = {
            "mode": mode,
            "provider_config": data,
            "job_id": job_id,
        }
        if custom_directive is not None:
            summarize_kwargs["custom_directive"] = custom_directive
        if custom_directive_enabled is not None:
            summarize_kwargs["custom_directive_enabled"] = custom_directive_enabled

        summary = summarize_text(article_text, **summarize_kwargs)

        # Step 4: Record into persistent summary history
        if is_serverless_env():
            summary["history_id"] = str(uuid.uuid4())
        else:
            try:
                entry = add_history_entry(
                    title=summary.get("title") or "Webpage Summary",
                    url=url,
                    mode=mode,
                    mode_name=summary.get("mode_name", mode.capitalize()),
                    summary=summary
                )
                if entry:
                    summary["history_id"] = entry["id"]
                    if job_id:
                        complete_job(job_id, summary, entry["id"])
                    try:
                        index_summary_entry(entry["id"], provider_config=data)
                    except Exception:
                        pass
            except Exception:
                pass

        if is_ajax:
            return jsonify({
                "success": True,
                "summary": summary,
                "url": url,
                "mode": mode,
                "provider": summary.get("provider"),
                "provider_name": summary.get("provider_name"),
                "model": summary.get("model"),
                "switched_to_fallback": summary.get("switched_to_fallback", False),
            })

        return render_template("index.html", summary=summary, url=url, mode=mode, modes=MODES, is_configured=has_valid_config(session))

    except ScraperError as e:
        error_msg = redact_credentials(str(e))
        if is_ajax:
            return jsonify({"error": error_msg}), 400
        return render_template("index.html", error=error_msg, url=url, mode=mode, modes=MODES, is_configured=has_valid_config(session)), 400

    except SummarizerError as e:
        error_msg = redact_credentials(str(e))
        if is_ajax:
            return jsonify({"error": error_msg}), 500
        return render_template("index.html", error=error_msg, url=url, mode=mode, modes=MODES, is_configured=has_valid_config(session)), 500

    except Exception as e:
        error_msg = redact_credentials(f"An unexpected error occurred: {str(e)}")
        if is_ajax:
            return jsonify({"error": error_msg}), 500
        return render_template("index.html", error=error_msg, url=url, mode=mode, modes=MODES, is_configured=has_valid_config(session)), 500


if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_DEBUG", "0").lower() in ("1", "true")
    port = int(os.getenv("PORT", 5000))
    host = os.getenv("HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=debug_mode)
