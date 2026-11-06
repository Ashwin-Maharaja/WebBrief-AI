import os
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
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

app = Flask(__name__)

# Secret key for secure, signed HttpOnly session cookies
app.secret_key = os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY") or "webbrief-ai-byok-v0.9-secure-session"
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)


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
    return render_template(
        "index.html",
        default_mode=DEFAULT_MODE,
        modes=MODES,
        is_configured=is_conf
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
    """API key settings and management page."""
    is_conf = has_valid_config(session_dict=session)
    is_serv = is_serverless_env()
    return render_template(
        "settings.html",
        is_configured=is_conf,
        is_serverless=is_serv
    )


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


@app.route("/summarize", methods=["POST"])
def summarize():
    # Support both AJAX JSON payloads and standard HTML form submissions
    if request.is_json:
        data = request.get_json() or {}
        url = data.get("url", "").strip()
        api_key = data.get("api_key", "").strip() or None
        mode = data.get("mode", DEFAULT_MODE).strip().lower()
        is_ajax = True
    else:
        url = request.form.get("url", "").strip()
        api_key = request.form.get("api_key", "").strip() or None
        mode = request.form.get("mode", DEFAULT_MODE).strip().lower()
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

        # Step 2: Generate AI summary in the chosen mode
        summary = summarize_text(article_text, api_key=api_key, mode=mode)

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
    app.run(debug=True)
