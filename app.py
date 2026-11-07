import datetime
import json
import os
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_file, Response, stream_with_context
from scraper import scrape_article, ScraperError
from summarizer import summarize_text, SummarizerError, MODES, DEFAULT_MODE
from byok import (
    has_valid_config,
    validate_openai_api_key,
    save_api_key_to_env,
    clear_saved_api_key,
    is_serverless_env,
    get_configured_api_key,
    redact_credentials,
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

import datetime
import json
import logging
import os
import re
import secrets
import urllib.parse
import uuid
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

logger = logging.getLogger(__name__)

APP_VERSION = "1.5.1"

app = Flask(__name__)

# Request payload limit: enforce 2MB maximum to prevent memory exhaustion / DoS attacks
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_CONTENT_LENGTH", 2 * 1024 * 1024))

# Secret key for secure, signed session cookies (SEC-01)
flask_secret = os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY")
is_prod = is_serverless_env() or os.getenv("FLASK_ENV") == "production" or os.getenv("ENVIRONMENT") == "production"

if not flask_secret:
    # In serverless/cloud hosting (Vercel) where FLASK_SECRET_KEY has not yet been set in the dashboard:
    # Derive a secure, unguessable key from Vercel deployment metadata or generate an ephemeral 32-byte token.
    # This prevents fatal 500 FUNCTION_INVOCATION_FAILED boot crashes while strictly avoiding predictable defaults.
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


@app.context_processor
def inject_globals():
    """Inject standard global template variables across all rendered views."""
    # Ensure CSRF token exists for session
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
    }


@app.before_request
def validate_csrf_and_origin():
    """
    Enforces Origin and CSRF validation on all state-changing requests (SEC-03).
    Verifies that Origin and Referer headers originate from the current host.
    """
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        # Check Origin header if present
        origin = request.headers.get("Origin")
        if origin:
            parsed_origin = urllib.parse.urlparse(origin)
            if parsed_origin.netloc and parsed_origin.netloc != request.host:
                return jsonify({"error": "Forbidden: Cross-origin request rejected."}), 403

        # Check Referer header if present and Origin was absent
        elif request.headers.get("Referer"):
            parsed_ref = urllib.parse.urlparse(request.headers.get("Referer"))
            if parsed_ref.netloc and parsed_ref.netloc != request.host:
                return jsonify({"error": "Forbidden: Invalid request origin."}), 403

        # For standard HTML form submissions, verify anti-CSRF token
        if not request.is_json and request.headers.get("X-Requested-With") != "XMLHttpRequest":
            # For HTML form posts (e.g. /settings/clear-key)
            submitted_token = request.form.get("csrf_token")
            expected_token = session.get("_csrf_token")
            if not submitted_token or not expected_token or not secrets.compare_digest(submitted_token, expected_token):
                # Only enforce for non-exempt form routes
                if request.endpoint in ("clear_key", "save_key"):
                    return jsonify({"error": "Forbidden: Invalid or missing CSRF token."}), 403


@app.before_request
def enforce_byok_onboarding():
    """
    Enforces the BYOK onboarding flow.
    If no valid OpenAI API configuration exists, redirects users to the dedicated /setup wizard.
    """
    # Exempt routes that must remain accessible without a configured key
    exempt_endpoints = {
        "setup",
        "settings",
        "save_key",
        "validate_key",
        "clear_key",
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
    }
    if request.endpoint in exempt_endpoints:
        return None

    # In hosted serverless mode (Vercel), allow visitors to access home directly
    if is_serverless_env() and request.endpoint in ("home",):
        return None

    # Check if a valid API key is present in environment or session
    if has_valid_config(session_dict=session):
        return None

    # Handle /summarize, /batch/summarize, /compare: check if user provided a per-request BYOK in payload
    if request.endpoint in ("summarize", "batch_summarize", "compare_articles_route"):
        key_in_req = None
        if request.is_json:
            key_in_req = (request.get_json() or {}).get("api_key")
        else:
            key_in_req = request.form.get("api_key")

        if key_in_req and str(key_in_req).strip():
            return None

        # If missing and AJAX, return 401 with redirect info
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({
                "error": "OpenAI API key not configured. Please complete setup wizard.",
                "redirect": url_for("setup")
            }), 401

        return redirect(url_for("setup"))

    # For page visits without configuration, redirect to setup wizard
    return redirect(url_for("setup"))


@app.route("/")
def home():
    is_conf = has_valid_config(session_dict=session)
    recent_history = [] if is_serverless_env() else get_history(limit=10)
    return render_template(
        "index.html",
        default_mode=DEFAULT_MODE,
        modes=MODES,
        is_configured=is_conf,
        recent_history=recent_history
    )


@app.route("/setup")
def setup():
    """First-run setup wizard and key update page."""
    mode = request.args.get("mode", "").strip().lower()
    is_conf = has_valid_config(session_dict=session)
    workspace_dir = os.path.dirname(os.path.abspath(__file__))
    env_exists = os.path.isfile(os.path.join(workspace_dir, ".env"))

    # If already configured and user visits /setup directly without mode=change, redirect to settings
    if is_conf and mode != "change":
        return redirect(url_for("settings"))

    return render_template(
        "setup.html",
        is_configured=is_conf,
        env_exists=env_exists,
        mode=mode
    )


@app.route("/settings")
def settings():
    """API key settings, custom prompt directives, and management page."""
    is_conf = has_valid_config(session_dict=session)
    is_serv = is_serverless_env()
    history_entries = [] if is_serv else get_history(limit=50)
    directives = get_custom_directives()
    return render_template(
        "settings.html",
        is_configured=is_conf,
        is_serverless=is_serv,
        history_count=len(history_entries),
        modes=MODES,
        default_mode=DEFAULT_MODE,
        custom_directive=directives.get("custom_directive", ""),
        custom_directive_enabled=directives.get("custom_directive_enabled", False),
        max_directive_length=MAX_DIRECTIVE_LENGTH,
    )


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
    """Returns the list of stored summary history entries."""
    if is_serverless_env():
        return jsonify({"history": []})
    entries = get_history(limit=50)
    return jsonify({"history": entries})


@app.route("/api/history/<entry_id>", methods=["GET"])
def api_get_history_entry(entry_id):
    """Retrieves a single summary entry to reopen."""
    if is_serverless_env():
        return jsonify({"error": "History entry not found."}), 404
    entry = get_history_entry(entry_id)
    if not entry:
        return jsonify({"error": "History entry not found."}), 404
    return jsonify({"entry": entry})


@app.route("/api/history/<entry_id>", methods=["DELETE"])
def api_delete_history(entry_id):
    """Deletes an individual history entry."""
    if is_serverless_env():
        return jsonify({"success": True})
    success = delete_history_entry(entry_id)
    if not success:
        return jsonify({"error": "Failed to delete history entry."}), 404
    return jsonify({"success": True})


@app.route("/api/history/clear", methods=["POST"])
def api_clear_history():
    """Clears all entries from summary history."""
    if is_serverless_env():
        return jsonify({"success": True})
    clear_history()
    return jsonify({"success": True})


# -------------------------------------------------------------
# PDF Document Export Endpoints
# -------------------------------------------------------------
@app.route("/export-pdf", methods=["POST"])
def export_pdf():
    """
    Generates and downloads a clean, multi-page PDF summary.
    Accepts JSON payload or Form data with structured summary data or a history_id.
    """
    if request.is_json:
        data = request.get_json() or {}
    else:
        data = request.form.to_dict()

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
    """Downloads a formatted PDF document directly for a saved history entry."""
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
    """Returns stored user preferences (custom prompt directives)."""
    return jsonify(get_custom_directives())


@app.route("/api/preferences", methods=["POST"])
def api_save_preferences():
    """
    Validates and persists user custom prompt directives.
    Enforces the MAX_DIRECTIVE_LENGTH limit.
    """
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
    """Resets user custom prompt directives to blank and disabled."""
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
    """
    Validates an array of URLs for batch processing.
    Identifies duplicate entries and format errors independently.
    """
    data = request.get_json() or {}
    raw_urls = data.get("urls", [])
    if not isinstance(raw_urls, list):
        return jsonify({"error": "Expected an array of URLs."}), 400

    items, stats = validate_batch_urls(raw_urls)
    return jsonify({"items": items, "stats": stats})


@app.route("/batch/summarize", methods=["POST"])
def batch_summarize():
    """
    Executes multi-URL batch summarization with optional streaming SSE progress.
    Reuses existing scraping, chunking, and summarization pipelines.
    """
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
        import queue
        import threading

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
                    )
                    batch_result_container.append(res)
                except Exception as e:
                    event_queue.put({"event": "error", "error": str(e)})
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
    )
    batch_result["success"] = True
    return jsonify(batch_result)


@app.route("/batch/export-pdf", methods=["POST"])
def batch_export_pdf():
    """
    Consolidates multiple summary briefs from a batch into a single downloadable PDF.
    """
    data = request.get_json() or {}
    items = data.get("items", [])

    # If history IDs were passed instead, resolve from database
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
        return jsonify({"error": f"Failed to generate batch PDF: {str(e)}"}), 500


# -------------------------------------------------------------
# Comparative Research Endpoints (v1.3)
# -------------------------------------------------------------
@app.route("/compare", methods=["POST"])
def compare_articles_route():
    """
    Executes deep comparative research across two independent URLs.
    Extracts common points, contradictions, differing perspectives,
    side-by-side claims, and overarching synthesis.
    """
    data = request.get_json() or {} if request.is_json else request.form
    url_a = data.get("url_a", "").strip()
    url_b = data.get("url_b", "").strip()

    if not url_a or not url_b:
        return jsonify({"error": "Please provide both Article A and Article B URLs for comparison."}), 400

    api_key = data.get("api_key") or get_configured_api_key(session_dict=session)
    if not api_key:
        return jsonify({
            "error": "OpenAI API key not configured. Please complete setup wizard.",
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
            api_key=api_key,
            custom_directive=custom_directive,
            custom_directive_enabled=custom_directive_enabled,
        )

        history_id = None
        if is_serverless_env():
            # In hosted mode, assign an ephemeral in-memory UUID and isolate from SQLite (SEC-05)
            history_id = str(uuid.uuid4())
        else:
            # Save comparative summary in local SQLite history
            art_b_title = comparison_res.get("article_b", {}).get("title", "Article B")
            history_entry = add_history_entry(
                title=comparison_res.get("title", "Comparative Analysis"),
                url=url_a,
                mode="comparison",
                mode_name="Comparative Research",
                summary=comparison_res,
                entry_type="comparison",
                url_b=url_b,
                title_b=art_b_title,
            )
            history_id = history_entry["id"] if history_entry else None
            if history_id:
                try:
                    index_summary_entry(history_id, api_key=api_key)
                except Exception:
                    pass

        return jsonify({
            "success": True,
            "comparison": comparison_res,
            "history_id": history_id,
        })
    except (ScraperError, ComparisonError, SummarizerError) as e:
        return jsonify({"error": redact_credentials(str(e))}), 400
    except Exception as e:
        return jsonify({"error": redact_credentials(f"Comparative analysis error: {str(e)}")}), 500


@app.route("/compare/export-pdf", methods=["POST"])
def compare_export_pdf():
    """Generates and downloads a formatted comparative research PDF brief."""
    data = request.get_json() or {}
    comp = data.get("comparison")

    # If history_id passed, fetch from DB only if local
    history_id = data.get("history_id")
    if not comp and history_id and not is_serverless_env():
        entry = get_history_entry(history_id)
        if entry:
            comp = entry.get("summary")

    if not comp or not isinstance(comp, dict):
        return jsonify({"error": "No comparative data provided for PDF export."}), 400

    try:
        pdf_buf = generate_comparison_pdf(comp)
        date_str = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        download_name = f"WebBrief_Comparison_{date_str}.pdf"
        return send_file(
            pdf_buf,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=download_name,
        )
    except Exception as e:
        return jsonify({"error": redact_credentials(f"Failed to generate comparison PDF: {str(e)}")}), 500


# -------------------------------------------------------------
# Notion Integration Endpoints (v1.3)
# -------------------------------------------------------------
@app.route("/api/notion/status", methods=["GET"])
def api_notion_status():
    """Checks whether Notion integration token is configured and valid."""
    provided_token = request.args.get("notion_token") or request.headers.get("X-Notion-Token")
    has_token = has_notion_config(session_dict=session, provided_token=provided_token)
    return jsonify({
        "is_configured": has_token,
        "has_token": has_token
    })


@app.route("/api/notion/connect", methods=["POST"])
def api_notion_connect():
    """Validates and saves the Notion integration token."""
    data = request.get_json() or {}
    token = data.get("token", "").strip()

    is_valid, msg = validate_notion_token(token)
    if not is_valid:
        return jsonify({"valid": False, "message": msg}), 400

    if is_serverless_env():
        # Hosted mode: token must stay client-side in browser memory; never save to cookies (SEC-02/04)
        return jsonify({"valid": True, "message": "Notion connected for current browser session.", "is_hosted": True})
    else:
        save_notion_key_to_env(token)
        return jsonify({"valid": True, "message": msg})


@app.route("/api/notion/disconnect", methods=["POST"])
def api_notion_disconnect():
    """Disconnects the Notion integration and removes stored credentials."""
    clear_saved_notion_key(session_dict=session)
    return jsonify({"success": True, "message": "Notion integration disconnected successfully."})


@app.route("/api/notion/pages", methods=["GET"])
def api_notion_pages():
    """Retrieves list of accessible destination Notion pages."""
    provided_token = request.args.get("notion_token") or request.headers.get("X-Notion-Token")
    token = get_notion_key(session_dict=session, provided_token=provided_token)
    if not token:
        return jsonify({"error": "Notion not connected. Please connect Notion in Settings.", "pages": []}), 401

    try:
        pages = get_accessible_pages(token)
        return jsonify({"pages": pages})
    except NotionError as ne:
        return jsonify({"error": redact_credentials(str(ne)), "pages": []}), 400
    except Exception as e:
        return jsonify({"error": redact_credentials(f"Failed to fetch Notion pages: {str(e)}"), "pages": []}), 500


@app.route("/api/notion/export", methods=["POST"])
def api_notion_export():
    """Exports a single summary brief or comparative research brief to Notion."""
    data = request.get_json() or {}
    provided_token = data.get("notion_token") or request.headers.get("X-Notion-Token")
    token = get_notion_key(session_dict=session, provided_token=provided_token)
    if not token:
        return jsonify({"error": "Notion is not connected. Please connect your Notion integration first."}), 401

    parent_page_id = data.get("parent_page_id", "").strip()
    export_type = data.get("type", "single")

    if not parent_page_id:
        return jsonify({"error": "Please select a destination Notion page."}), 400

    try:
        if export_type == "comparison":
            comp = data.get("comparison")
            if not comp and data.get("history_id") and not is_serverless_env():
                entry = get_history_entry(data.get("history_id"))
                if entry:
                    comp = entry.get("summary")
            if not comp:
                return jsonify({"error": "No comparison data provided for Notion export."}), 400

            title = comp.get("title", "Comparative Research Brief")
            blocks = format_comparison_blocks(comp)
            res = create_notion_page(parent_page_id, page_title=title, blocks=blocks, token=token, emoji_icon="⚖️")
            return jsonify({"success": True, "page_url": res["url"], "title": title})
        else:
            summary = data.get("summary")
            title = data.get("title", "Webpage Summary")
            url = data.get("url", "")
            mode = data.get("mode", DEFAULT_MODE)
            mode_name = MODES.get(mode, {}).get("name", "Summary")

            if not summary and data.get("history_id") and not is_serverless_env():
                entry = get_history_entry(data.get("history_id"))
                if entry:
                    summary = entry.get("summary")
                    title = entry.get("title", title)
                    url = entry.get("url", url)
                    mode = entry.get("mode", mode)
                    mode_name = entry.get("mode_name", mode_name)

            if not summary:
                return jsonify({"error": "No summary data provided for Notion export."}), 400

            blocks = format_single_summary_blocks(summary, title=title, url=url, mode_name=mode_name)
            res = create_notion_page(parent_page_id, page_title=title, blocks=blocks, token=token, emoji_icon="🌐")
            return jsonify({"success": True, "page_url": res["url"], "title": title})

    except NotionError as ne:
        return jsonify({"error": redact_credentials(str(ne))}), 400
    except Exception as e:
        return jsonify({"error": redact_credentials(f"Failed to export to Notion: {str(e)}")}), 500


# -------------------------------------------------------------
# Local Knowledge Retrieval & Semantic Search Endpoints (v1.4)
# -------------------------------------------------------------
@app.route("/api/search/semantic", methods=["POST"])
def api_search_semantic():
    """
    Performs natural-language semantic vector retrieval over saved summaries.
    Computes exact dot-product cosine similarity against query embedding.
    """
    if is_serverless_env():
        return jsonify({
            "error": "Server-side semantic search is disabled in hosted mode. Please use client-side search via /api/search/embed."
        }), 400

    data = request.get_json() or {}
    query = data.get("query", "").strip()

    if not query:
        return jsonify({"error": "Please enter a search query."}), 400

    api_key = data.get("api_key") or get_configured_api_key(session_dict=session)
    if not api_key:
        return jsonify({
            "error": "OpenAI API key required to embed search queries. Please configure your key in Settings.",
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
            api_key=api_key,
            top_k=top_k,
            threshold=threshold,
            filter_type=filter_type,
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
    """Returns local vector index statistics."""
    if is_serverless_env():
        return jsonify({"indexed_count": 0, "total_summaries": 0, "status": "hosted_indexeddb"})
    stats = get_vector_store_stats()
    return jsonify(stats)


@app.route("/api/search/rebuild-index", methods=["POST"])
def api_search_rebuild_index():
    """Rebuilds vector embeddings across all saved summaries in local history."""
    if is_serverless_env():
        return jsonify({"error": "Vector indexing on server is disabled in hosted mode."}), 400

    data = request.get_json() or {}
    api_key = data.get("api_key") or get_configured_api_key(session_dict=session)
    if not api_key:
        return jsonify({
            "error": "OpenAI API key required to generate embeddings. Please configure your key in Settings.",
            "redirect": url_for("setup")
        }), 401

    try:
        result = rebuild_vector_index(api_key=api_key)
        return jsonify({
            "success": True,
            "stats": result,
            "message": f"Successfully indexed {result.get('indexed', 0)} of {result.get('total', 0)} summaries."
        })
    except Exception as e:
        return jsonify({"error": redact_credentials(f"Failed to rebuild index: {str(e)}")}), 500


@app.route("/api/search/embed", methods=["POST"])
def api_search_embed():
    """Generates an embedding vector for a snippet of text (useful for client-side search)."""
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "No text provided to embed."}), 400

    api_key = data.get("api_key") or get_configured_api_key(session_dict=session)
    if not api_key:
        return jsonify({"error": "OpenAI API key required."}), 401

    try:
        vector = generate_embedding(text, api_key=api_key)
        return jsonify({
            "embedding": vector,
            "model": DEFAULT_EMBEDDING_MODEL,
            "dimensions": len(vector),
        })
    except Exception as e:
        return jsonify({"error": redact_credentials(f"Embedding generation failed: {str(e)}")}), 500
        return jsonify({"error": f"Embedding generation failed: {str(e)}"}), 500


@app.route("/api/environment", methods=["GET"])
def api_environment():
    """Returns deployment mode details (hosted serverless vs local self-hosted)."""
    hosted = is_serverless_env()
    return jsonify({
        "version": APP_VERSION,
        "is_hosted": hosted,
        "storage": "indexeddb" if hosted else "sqlite",
        "storage_mode": "indexeddb" if hosted else "sqlite",
        "byok_mode": "session_memory" if hosted else "env_file",
        "ssrf_guard": "active"
    })


# -------------------------------------------------------------
# BYOK Configuration Endpoints
# -------------------------------------------------------------
@app.route("/api/validate-key", methods=["POST"])
def validate_key():
    """AJAX endpoint to test and validate an API key without saving."""
    data = request.get_json() or {} if request.is_json else request.form
    api_key = data.get("api_key", "").strip()

    is_valid, message = validate_openai_api_key(api_key)
    status_code = 200 if is_valid else 400
    return jsonify({"valid": is_valid, "message": message}), status_code


@app.route("/api/save-key", methods=["POST"])
def save_key():
    """
    Validates the submitted OpenAI API key.
    In local self-hosted mode: saves it into the local .env configuration file.
    In hosted serverless mode: verifies the key and returns success for in-memory session usage.
    """
    if request.is_json:
        data = request.get_json() or {}
        api_key = data.get("api_key", "").strip()
        confirm_overwrite = bool(data.get("confirm_overwrite", False))
        is_ajax = True
    else:
        api_key = request.form.get("api_key", "").strip()
        confirm_overwrite = request.form.get("confirm_overwrite") in ("true", "1", "on")
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    # Step 1: Validate key with minimal 0-token verification
    is_valid, val_message = validate_openai_api_key(api_key)
    if not is_valid:
        if is_ajax:
            return jsonify({"success": False, "error": val_message}), 400
        workspace_dir = os.path.dirname(os.path.abspath(__file__))
        env_exists = os.path.isfile(os.path.join(workspace_dir, ".env"))
        return render_template("setup.html", error=val_message, is_configured=has_valid_config(session), env_exists=env_exists), 400

    # In hosted serverless mode (Vercel), do not touch filesystem or session storage
    if is_serverless_env():
        if is_ajax:
            return jsonify({
                "success": True,
                "message": "API key verified for active browser session.",
                "is_hosted": True,
                "redirect": url_for("home")
            })
        return redirect(url_for("home"))

    # Step 2: Save key safely into .env / session
    success, save_message = save_api_key_to_env(
        api_key=api_key,
        confirm_overwrite=confirm_overwrite,
        session_dict=session
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
    """Removes the saved OpenAI API key from configuration."""
    clear_saved_api_key(session_dict=session)
    return redirect(url_for("setup"))


# -------------------------------------------------------------
# Webpage Summarization Endpoint
# -------------------------------------------------------------
@app.route("/summarize", methods=["POST"])
def summarize():
    # Support both AJAX JSON payloads and standard HTML form submissions
    if request.is_json:
        data = request.get_json() or {}
        url = data.get("url", "").strip()
        api_key = data.get("api_key", "").strip() or None
        mode = data.get("mode", DEFAULT_MODE).strip().lower()
        custom_directive = data.get("custom_directive")
        custom_directive_enabled = data.get("custom_directive_enabled")
        is_ajax = True
    else:
        url = request.form.get("url", "").strip()
        api_key = request.form.get("api_key", "").strip() or None
        mode = request.form.get("mode", DEFAULT_MODE).strip().lower()
        custom_directive = request.form.get("custom_directive")
        custom_directive_enabled = request.form.get("custom_directive_enabled")
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    # Normalize mode
    if mode not in MODES:
        mode = DEFAULT_MODE

    # Validation: prevent empty submissions
    if not url:
        error_msg = "Please enter a webpage URL."
        if is_ajax:
            return jsonify({"error": error_msg}), 400
        return render_template("index.html", error=error_msg, url="", mode=mode, modes=MODES, is_configured=has_valid_config(session)), 400

    try:
        # Step 1: Scrape article text from webpage
        article_text = scrape_article(url)

        # Step 2: Generate AI summary in the chosen mode with optional custom directives
        summarize_kwargs = {
            "api_key": api_key,
            "mode": mode,
        }
        if custom_directive is not None:
            summarize_kwargs["custom_directive"] = custom_directive
        if custom_directive_enabled is not None:
            summarize_kwargs["custom_directive_enabled"] = custom_directive_enabled

        summary = summarize_text(article_text, **summarize_kwargs)

        # Step 3: Record into persistent summary history
        if is_serverless_env():
            # In hosted serverless mode (Vercel), assign an in-memory UUID and isolate from SQLite (SEC-05)
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
                    try:
                        effective_key = api_key or get_configured_api_key(session_dict=session)
                        index_summary_entry(entry["id"], api_key=effective_key)
                    except Exception:
                        pass
            except Exception:
                # History recording should not break summarization return
                pass

        if is_ajax:
            return jsonify({
                "success": True,
                "summary": summary,
                "url": url,
                "mode": mode
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
