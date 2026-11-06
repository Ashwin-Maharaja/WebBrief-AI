import json
import os
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_file
from scraper import scrape_article, ScraperError
from summarizer import summarize_text, SummarizerError, MODES, DEFAULT_MODE
from byok import (
    has_valid_config,
    validate_openai_api_key,
    save_api_key_to_env,
    clear_saved_api_key,
    is_serverless_env,
    get_configured_api_key,
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
from pdf_exporter import generate_summary_pdf, build_pdf_filename

APP_VERSION = "1.1"

app = Flask(__name__)

# Secret key for secure, signed HttpOnly session cookies
app.secret_key = os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY") or "webbrief-ai-byok-v1.0-production-session"
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "false").lower() in ("true", "1"),
)


@app.context_processor
def inject_globals():
    """Inject standard global template variables across all rendered views."""
    return {
        "app_version": APP_VERSION,
        "default_mode": DEFAULT_MODE,
        "modes": MODES,
    }


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
    }
    if request.endpoint in exempt_endpoints:
        return None

    # Check if a valid API key is present in environment or session
    if has_valid_config(session_dict=session):
        return None

    # Handle /summarize: check if user provided a per-request BYOK in payload
    if request.endpoint == "summarize":
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
    recent_history = get_history(limit=10)
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
    history_entries = get_history(limit=50)
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
    entries = get_history(limit=50)
    return render_template("history.html", entries=entries)


# -------------------------------------------------------------
# Summary History API Endpoints
# -------------------------------------------------------------
@app.route("/api/history", methods=["GET"])
def api_get_history():
    """Returns the list of stored summary history entries."""
    entries = get_history(limit=50)
    return jsonify({"history": entries})


@app.route("/api/history/<entry_id>", methods=["GET"])
def api_get_history_entry(entry_id):
    """Retrieves a single summary entry to reopen."""
    entry = get_history_entry(entry_id)
    if not entry:
        return jsonify({"error": "History entry not found."}), 404
    return jsonify({"entry": entry})


@app.route("/api/history/<entry_id>", methods=["DELETE"])
def api_delete_history(entry_id):
    """Deletes an individual history entry."""
    success = delete_history_entry(entry_id)
    if not success:
        return jsonify({"error": "Failed to delete history entry."}), 404
    return jsonify({"success": True})


@app.route("/api/history/clear", methods=["POST"])
def api_clear_history():
    """Clears all entries from summary history."""
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

    if history_id:
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
        return jsonify({"error": f"Failed to generate PDF: {str(e)}"}), 500


@app.route("/export-pdf/<entry_id>", methods=["GET"])
def export_pdf_by_id(entry_id):
    """Downloads a formatted PDF document directly for a saved history entry."""
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
        return jsonify({"error": f"Failed to generate PDF: {str(e)}"}), 500


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
    Validates the submitted OpenAI API key, and upon success,
    saves it into the local .env configuration file (or session if serverless).
    Requires explicit confirmation before replacing an existing key.
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
        error_msg = str(e)
        if is_ajax:
            return jsonify({"error": error_msg}), 400
        return render_template("index.html", error=error_msg, url=url, mode=mode, modes=MODES, is_configured=has_valid_config(session)), 400

    except SummarizerError as e:
        error_msg = str(e)
        if is_ajax:
            return jsonify({"error": error_msg}), 500
        return render_template("index.html", error=error_msg, url=url, mode=mode, modes=MODES, is_configured=has_valid_config(session)), 500

    except Exception as e:
        error_msg = f"An unexpected error occurred: {str(e)}"
        if is_ajax:
            return jsonify({"error": error_msg}), 500
        return render_template("index.html", error=error_msg, url=url, mode=mode, modes=MODES, is_configured=has_valid_config(session)), 500


if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_DEBUG", "0").lower() in ("1", "true")
    port = int(os.getenv("PORT", 5000))
    host = os.getenv("HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=debug_mode)
