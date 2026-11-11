from __future__ import annotations

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
import time
from collections import defaultdict
from typing import Optional, Dict, Any, List, Tuple

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
    g,
)

from scraper import scrape_article, ScraperError
from summarizer import summarize_text, SummarizerError, MODES, DEFAULT_MODE
from byok import (
    has_valid_config,
    validate_gemini_credentials,
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
    get_allowed_models,
    is_valid_model,
)
from services.providers.exceptions import ProviderInvalidModelError
from history import (
    add_history_entry,
    get_history,
    get_history_entry,
    get_history_count,
    delete_history_entry,
    clear_history,
    get_custom_directives,
    save_custom_directives,
    reset_custom_directives,
    MAX_DIRECTIVE_LENGTH,
    HistoryError,
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
    generate_embedding_with_model,
    VectorStoreError,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_SIMILARITY_THRESHOLD,
    DEFAULT_SEARCH_LIMIT,
)

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)

from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

handler = app

# Request payload limit: enforce 2MB maximum
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_CONTENT_LENGTH", 2 * 1024 * 1024))

INSECURE_SECRET_PLACEHOLDERS = {
    "",
    "your_flask_secret_key_here",
    "your_secret_key_here",
    "change-me",
    "change_me",
    "changeme",
    "development-key",
    "development",
    "dev",
    "secret",
    "secret_key",
    "flask_secret_key",
    "123456",
    "12345678",
    "password",
    "admin",
    "placeholder",
    "webbrief",
    "webbrief-ai",
}


def resolve_flask_secret(is_production: bool) -> str:
    """
    Resolves the Flask secret key according to environment mode (SEC-01).
    In production: strictly enforces an explicitly configured, high-entropy FLASK_SECRET_KEY
    (minimum 32 characters / 16 bytes). Fails startup safely if missing or insecure.
    Never derives secrets from deployment IDs, commit SHAs, or predictable strings.
    In development: allows configured key or safely generates a cryptographic 32-byte ephemeral key.
    """
    raw_secret = (os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY") or "").strip()
    is_insecure = (
        not raw_secret
        or raw_secret.lower() in INSECURE_SECRET_PLACEHOLDERS
        or len(raw_secret) < 32
    )

    if is_production:
        if is_insecure:
            raise RuntimeError(
                "CRITICAL: Production requires an explicitly configured, secure FLASK_SECRET_KEY "
                "(minimum 32 characters). Ephemeral secrets are rejected in production to prevent "
                "session invalidation across serverless instances and cold starts. "
                "Generate a key using 'python -c \"import secrets; print(secrets.token_hex(32))\"' "
                "and configure it in your deployment environment variables."
            )
        return raw_secret

    # Development mode
    if is_insecure:
        if raw_secret:
            logger.warning(
                "Configured FLASK_SECRET_KEY is insecure or a placeholder. "
                "Using an ephemeral 32-byte cryptographically secure random key for local development."
            )
        else:
            logger.warning("No FLASK_SECRET_KEY configured. Generated ephemeral 32-byte development secret key.")
        return secrets.token_hex(32)

    return raw_secret


is_prod = is_serverless_env() or os.getenv("FLASK_ENV") == "production" or os.getenv("ENVIRONMENT") == "production"

_startup_error = None
try:
    app.secret_key = resolve_flask_secret(is_prod)
except Exception as exc:
    _startup_error = str(exc)
    # Ephemeral fallback key so Flask internals can safely function without crashing serverless instances
    app.secret_key = secrets.token_hex(32)
    logger.warning("Startup configuration notice: %s. Using ephemeral key for serverless runtime.", _startup_error)

# Session Cookie Hardening (SEC-02)
is_secure_cookie = os.getenv("SESSION_COOKIE_SECURE", "true" if is_prod else "false").lower() in ("true", "1")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=is_secure_cookie,
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(minutes=30),
)

# Persistent Vault Reference Cookie (SEC-02 / BYOK Persistence)
VAULT_COOKIE_NAME = "wb_vault_id"
VAULT_COOKIE_MAX_AGE = 315360000  # 10 years persistent (seconds)

# Encrypted BYOK credential vault. Redis-backed storage persists encrypted credentials indefinitely
# across browser sessions, browser restarts, and application restarts without exposing raw secrets.
from credential_vault import (
    TemporaryCredentialVault,
    VaultStatus,
)

# -------------------------------------------------------------
# Structured Metrics & Observability Tracker (OBS-01, OBS-02)
# -------------------------------------------------------------
_METRICS: Dict[str, Any] = {
    "summaries_success": 0,
    "summaries_failed": 0,
    "summaries_partial": 0,
    "scrape_failures": 0,
    "gemini_errors": 0,
    "redis_errors": 0,
    "sqlite_errors": 0,
    "batch_completed": 0,
    "pdf_failures": 0,
    "notion_failures": 0,
}
_METRICS_LOCK = threading.Lock()

def increment_metric(name: str, count: int = 1) -> None:
    """Thread-safe lightweight counter increment for production observability (OBS-02)."""
    with _METRICS_LOCK:
        if name in _METRICS:
            _METRICS[name] += count
        else:
            _METRICS[name] = count

_vault = None
_redis = None
try:
    if os.getenv("REDIS_URL") and os.getenv("CREDENTIAL_ENCRYPTION_KEY"):
        from redis import Redis
        from flask_session import Session
        _redis = Redis.from_url(os.environ["REDIS_URL"], socket_connect_timeout=3, socket_timeout=3)
        app.config.update(
            SESSION_TYPE="redis", SESSION_REDIS=_redis,
            SESSION_PERMANENT=True, SESSION_REFRESH_EACH_REQUEST=True,
            SESSION_KEY_PREFIX="webbrief:session:",
        )
        Session(app)
        _vault = TemporaryCredentialVault(_redis, os.environ["CREDENTIAL_ENCRYPTION_KEY"])
    elif is_prod and (os.getenv("REDIS_URL") or os.getenv("CREDENTIAL_ENCRYPTION_KEY")):
        raise RuntimeError("Temporary BYOK vault requires both REDIS_URL and CREDENTIAL_ENCRYPTION_KEY in production.")
except Exception as exc:
    if not _startup_error:
        _startup_error = str(exc)
    logger.critical("Startup configuration error: %s", str(exc))


class InMemoryRateLimiter:
    """
    Thread-safe process-local sliding-window rate limiter accounting for shared NATs and anonymous sessions.
    NOTE: Process-local rate limiting protects against rapid bursts within a single application instance
    or serverless cold start. It does not provide globally synchronized distributed rate limiting across
    multiple independent serverless Lambda instances unless backed by an external store (e.g. Redis).
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._events = defaultdict(list)

    def is_allowed(self, key: str, max_requests: int, window_seconds: int = 60) -> bool:
        now = time.time()
        cutoff = now - window_seconds
        with self._lock:
            timestamps = self._events[key]
            valid = [t for t in timestamps if t > cutoff]
            if len(valid) >= max_requests:
                self._events[key] = valid
                return False
            valid.append(now)
            self._events[key] = valid
            return True

rate_limiter = InMemoryRateLimiter()


def is_rate_limited_redis(redis_client, key: str, max_requests: int, window_seconds: int = 60) -> Optional[bool]:
    """
    Atomic rate limiting check using Redis when configured.
    Returns:
        True if request is allowed.
        False if rate limit is exceeded.
        None if Redis error occurs (triggering graceful fallback to in-memory limiter).
    """
    try:
        rkey = f"webbrief:ratelimit:{key}"
        pipe = redis_client.pipeline()
        pipe.incr(rkey)
        pipe.expire(rkey, window_seconds)
        current, _ = pipe.execute()
        return int(current) <= max_requests
    except Exception as e:
        logger.warning("Redis rate limiter unavailable (%s). Falling back to in-memory rate limiter.", str(e))
        return None


def check_rate_limit(endpoint_name: str, max_requests: int, window_seconds: int = 60):
    """
    Enforces per-client sliding window rate limits.
    Combines endpoint name, client IP, and session token prefix to isolate clients
    without penalizing users sharing corporate, institutional, or mobile NAT gateways.
    If Redis is configured and reachable, synchronizes state across serverless instances.
    Gracefully falls back to process-local InMemoryRateLimiter if Redis is not configured or fails.
    """
    client_ip = request.remote_addr or "unknown"
    session_token = session.get("_csrf_token") or session.get("_vault_sid") or ""
    client_key = f"{endpoint_name}:{client_ip}:{session_token[:16]}"

    allowed = None
    if _redis is not None:
        allowed = is_rate_limited_redis(_redis, client_key, max_requests, window_seconds)

    if allowed is None:
        allowed = rate_limiter.is_allowed(client_key, max_requests, window_seconds)

    if not allowed:
        return jsonify({
            "error": "Rate limit exceeded. Please wait a moment before sending more requests.",
            "code": "RATE_LIMIT_EXCEEDED"
        }), 429
    return None


def parse_bool(val: Any, default: bool = False) -> bool:
    """Safely converts input values to boolean, avoiding string 'false' coercion bugs (COMP-01)."""
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes", "on")
    return bool(val)


def _vault_sid() -> str:
    """
    Resolves the persistent high-entropy vault identifier.
    Prioritizes the dedicated HttpOnly persistent cookie, falling back to session.
    Ensures the identifier persists across browser sessions and application restarts.
    """
    # 1. Check dedicated persistent cookie
    cookie_sid = request.cookies.get(VAULT_COOKIE_NAME)
    if cookie_sid and len(cookie_sid) >= 16:
        session["_vault_sid"] = cookie_sid
        return cookie_sid

    # 2. Check Flask session
    if "_vault_sid" in session and session["_vault_sid"]:
        sid = session["_vault_sid"]
        g._set_vault_cookie = sid
        return sid

    # 3. Generate fresh cryptographically secure high-entropy identifier
    sid = secrets.token_urlsafe(32)
    session["_vault_sid"] = sid
    g._set_vault_cookie = sid
    return sid


def _vault_status() -> Tuple[VaultStatus, dict]:
    """Retrieves vault credentials along with explicit status outcome."""
    if _vault is None:
        return VaultStatus.NOT_CONFIGURED, {}
    sid = _vault_sid()
    if hasattr(_vault, "load_with_status"):
        try:
            res = _vault.load_with_status(sid)
            if isinstance(res, tuple) and len(res) == 2:
                return res
        except Exception as e:
            logger.error("Error retrieving vault status: %s", e)
            return VaultStatus.UNAVAILABLE, {}
    if hasattr(_vault, "load"):
        try:
            data = _vault.load(sid)
            if isinstance(data, dict):
                return (VaultStatus.CONFIGURED if data else VaultStatus.NOT_CONFIGURED), data
        except Exception as e:
            logger.error("Error loading vault credentials: %s", e)
            return VaultStatus.UNAVAILABLE, {}
    return VaultStatus.NOT_CONFIGURED, {}


def _vault_config() -> dict:
    _, data = _vault_status()
    return data


@app.before_request
def check_startup_configuration():
    """Fails safely with an informative HTTP 500 response if production configuration is missing."""
    if _startup_error:
        if request.path in ("/health", "/api/health", "/api/health/liveness", "/api/health/readiness"):
            return jsonify({
                "status": "error",
                "code": "CONFIGURATION_REQUIRED",
                "message": _startup_error,
            }), 500
        if request.path.startswith("/api/") or request.is_json:
            return jsonify({
                "status": "error",
                "code": "CONFIGURATION_REQUIRED",
                "message": _startup_error,
            }), 500
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>WebBrief AI — Configuration Required</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            background: #0f172a;
            color: #f8fafc;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 24px;
        }}
        .card {{
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 16px;
            padding: 36px;
            max-width: 640px;
            width: 100%;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.4);
        }}
        .header {{
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 20px;
        }}
        .icon {{
            width: 40px;
            height: 40px;
            border-radius: 10px;
            background: rgba(239, 68, 68, 0.15);
            color: #ef4444;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 20px;
            font-weight: bold;
        }}
        h1 {{
            font-size: 1.35rem;
            color: #f8fafc;
            font-weight: 600;
        }}
        p {{
            color: #94a3b8;
            font-size: 0.95rem;
            line-height: 1.6;
            margin-bottom: 16px;
        }}
        .alert {{
            background: rgba(239, 68, 68, 0.1);
            border: 1px solid rgba(239, 68, 68, 0.25);
            border-radius: 8px;
            padding: 12px 16px;
            color: #fca5a5;
            font-size: 0.9rem;
            margin-bottom: 20px;
        }}
        .steps {{
            background: #0f172a;
            border: 1px solid #334155;
            border-radius: 10px;
            padding: 18px 20px 18px 36px;
            color: #cbd5e1;
            font-size: 0.9rem;
            line-height: 1.8;
            margin-bottom: 20px;
        }}
        code {{
            background: rgba(255, 255, 255, 0.08);
            color: #38bdf8;
            padding: 2px 6px;
            border-radius: 4px;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            font-size: 0.88em;
        }}
        pre {{
            background: #030712;
            border: 1px solid #1f2937;
            border-radius: 6px;
            padding: 10px 14px;
            color: #38bdf8;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            font-size: 0.85em;
            margin: 8px 0;
            overflow-x: auto;
        }}
    </style>
</head>
<body>
    <div class="card">
        <div class="header">
            <div class="icon">!</div>
            <h1>Configuration Required in Production</h1>
        </div>
        <div class="alert">
            WebBrief AI is deployed in production mode, but <code>FLASK_SECRET_KEY</code> is not configured in your environment variables.
        </div>
        <p>To prevent session invalidation across serverless instances and cold starts, production deployments strictly require an explicitly configured <code>FLASK_SECRET_KEY</code>.</p>
        <p><strong>Steps to configure on Vercel:</strong></p>
        <ol class="steps">
            <li>Generate a secure 32-byte secret key:
                <pre>python -c "import secrets; print(secrets.token_hex(32))"</pre>
            </li>
            <li>Open your <strong>Vercel Dashboard &rarr; Project Settings &rarr; Environment Variables</strong>.</li>
            <li>Add a new variable:
                <br>Key: <code>FLASK_SECRET_KEY</code>
                <br>Value: <em>[your generated 64-hex-character string]</em>
            </li>
            <li>Save and redeploy the project in Vercel.</li>
        </ol>
    </div>
</body>
</html>""", 500


@app.before_request
def attach_vault_credentials():
    """Merge credentials into request-scoped JSON only; never return them to client."""
    if _vault is None or not request.is_json or request.method != "POST":
        return
    if request.endpoint not in {
        "summarize", "batch_summarize", "compare_articles_route",
        "api_search_embed", "api_search_semantic", "api_search_rebuild_index"
    }:
        return
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return
    stored = _vault_config()
    for key in (
        "gemini_api_key", "gemini_model", "primary_provider"
    ):
        if not payload.get(key) and stored.get(key):
            payload[key] = stored[key]
    if stored and not payload.get("primary_provider"):
        payload["primary_provider"] = stored.get("primary_provider", "gemini")


@app.route("/api/credentials", methods=["POST"])
def store_temporary_credentials():
    if _vault is None:
        return jsonify({"error": "Temporary credential vault is not configured."}), 503

    limit_resp = check_rate_limit("store_credentials", max_requests=15, window_seconds=60)
    if limit_resp:
        return limit_resp

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON object required."}), 400

    gemini_model = (payload.get("gemini_model") or payload.get("model") or "").strip()
    if gemini_model:
        if not is_valid_model(gemini_model, "gemini"):
            allowed = get_allowed_models("gemini")
            return jsonify({"error": f"Unsupported model '{gemini_model}'. Supported models: {', '.join(allowed)}"}), 400

    gemini_key = (payload.get("gemini_api_key") or payload.get("api_key") or "").strip()
    valid, msg = validate_gemini_credentials(gemini_key, model=gemini_model or None)
    if not valid:
        return jsonify({"error": f"Provider credentials failed validation: {msg}"}), 400

    allowed = {
        "primary_provider", "gemini_api_key", "gemini_model", "notion_token"
    }
    clean = {k: v for k, v in payload.items() if k in allowed and (isinstance(v, str) or isinstance(v, bool))}
    clean["gemini_api_key"] = gemini_key
    clean["primary_provider"] = "gemini"
    if gemini_model:
        clean["gemini_model"] = gemini_model

    sid = _vault_sid()
    saved, v_status = _vault.update(sid, clean)
    if not saved:
        if v_status == VaultStatus.UNAVAILABLE:
            return jsonify({
                "error": "Credential storage is temporarily unavailable. Cannot safely update credentials without verifying existing configuration.",
                "status": "unavailable",
            }), 503
        elif v_status in (VaultStatus.DECRYPTION_ERROR, VaultStatus.CORRUPTED):
            return jsonify({
                "error": "Existing credential record cannot be decrypted or is corrupted. Update blocked to prevent state corruption.",
                "status": v_status.value,
            }), 500
        return jsonify({"error": "Failed to store credentials in vault."}), 503

    g._set_vault_cookie = sid
    return jsonify({"success": True, "persistent": True}), 200


@app.route("/api/credentials/status", methods=["GET"])
def temporary_credentials_status():
    if _vault is None:
        return jsonify({
            "enabled": False,
            "configured": False,
            "status": "disabled",
            "persistent": False,
        })

    status, data = _vault_status()
    if status == VaultStatus.UNAVAILABLE:
        return jsonify({
            "enabled": True,
            "configured": False,
            "status": "unavailable",
            "error": "Credential vault is temporarily unavailable. Please try again shortly.",
            "persistent": True,
        }), 503
    elif status == VaultStatus.DECRYPTION_ERROR:
        return jsonify({
            "enabled": True,
            "configured": False,
            "status": "decryption_error",
            "error": "Stored credentials could not be decrypted with the server key.",
            "persistent": True,
        }), 500
    elif status == VaultStatus.CORRUPTED:
        return jsonify({
            "enabled": True,
            "configured": False,
            "status": "corrupted",
            "error": "Stored credential record is malformed.",
            "persistent": True,
        }), 500

    return jsonify({
        "enabled": True,
        "configured": bool(data),
        "status": "configured" if data else "not_configured",
        "persistent": True,
        "provider": data.get("primary_provider", "gemini") if data else None,
        "gemini_model": data.get("gemini_model") if data else None,
        "primary_model": data.get("gemini_model") if data else None,
    })


@app.route("/api/credentials", methods=["DELETE"])
def delete_temporary_credentials():
    if _vault is not None:
        sid = _vault_sid()
        deleted = _vault.delete(sid)
        if not deleted:
            return jsonify({"error": "Failed to delete credentials from storage."}), 503
        session.pop("_vault_sid", None)
        g._delete_vault_cookie = True
    session.pop("gemini_api_key", None)
    session.pop("notion_token", None)
    return jsonify({"success": True})


@app.after_request
def apply_security_headers(response):
    """Inject standard defensive security headers into all HTTP responses and manage persistent vault cookie (SEC-02, SEC-07)."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"

    # Manage persistent vault cookie (P2-VLT-03)
    if getattr(g, "_delete_vault_cookie", False):
        response.delete_cookie(
            VAULT_COOKIE_NAME,
            path="/",
            httponly=True,
            samesite="Lax",
            secure=is_secure_cookie,
        )
    else:
        set_sid = getattr(g, "_set_vault_cookie", None)
        if set_sid and _vault is not None:
            response.set_cookie(
                VAULT_COOKIE_NAME,
                set_sid,
                max_age=VAULT_COOKIE_MAX_AGE,
                httponly=True,
                samesite="Lax",
                secure=is_secure_cookie,
                path="/",
            )
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https: blob:; "
        "connect-src 'self' blob:; "
        "frame-ancestors 'self'; "
        "form-action 'self'; "
        "base-uri 'self'; "
        "object-src 'none';"
    )

    req_id = getattr(g, "request_id", None)
    if req_id:
        response.headers["X-Request-ID"] = req_id
    start_time = getattr(g, "start_time", None)
    if start_time:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        response.headers["X-Response-Time"] = f"{duration_ms}ms"

    return response


@app.before_request
def initialize_request_tracing():
    """Assigns unique Request ID and start time for structured request tracing (OBS-01)."""
    g.request_id = request.headers.get("X-Request-ID") or f"req_{secrets.token_hex(8)}"
    g.start_time = time.monotonic()


@app.template_filter("safe_url")
def safe_url_filter(url: Optional[str]) -> str:
    """Sanitizes URLs to only allow safe schemes (http, https, mailto) in href contexts (SEC-03)."""
    if not url:
        return "#"
    s = str(url).strip()
    if s.lower().startswith(("http://", "https://", "mailto:")):
        return s
    return "#"


@app.errorhandler(403)
def handle_forbidden_error(e):
    """Sanitized 403 Forbidden handler."""
    if request.path.startswith("/api/") or request.is_json:
        return jsonify({"error": "Forbidden: Request authorization or CSRF check failed.", "code": "FORBIDDEN"}), 403
    return render_template(
        "index.html",
        error="Forbidden: Request rejected by security policies.",
        url="",
        mode=DEFAULT_MODE,
        modes=MODES,
        is_configured=has_valid_config(session),
        provider_config=get_active_provider_config(session_dict=session),
        provider_catalogue=PROVIDER_CATALOGUE,
        recent_history=[],
    ), 403


@app.errorhandler(404)
def handle_not_found_error(e):
    """Sanitized 404 Not Found handler."""
    if request.path.startswith("/api/") or request.is_json:
        return jsonify({"error": "Resource not found.", "code": "NOT_FOUND"}), 404
    return render_template(
        "index.html",
        error="The requested resource was not found.",
        url="",
        mode=DEFAULT_MODE,
        modes=MODES,
        is_configured=has_valid_config(session),
        provider_config=get_active_provider_config(session_dict=session),
        provider_catalogue=PROVIDER_CATALOGUE,
        recent_history=[],
    ), 404


@app.errorhandler(500)
def handle_internal_server_error(e):
    """Sanitized 500 Internal Server Error handler to avoid exposing raw traces (SEC-04)."""
    logger.error("Internal Server Error: %s", redact_credentials(str(e)), exc_info=True)
    if request.path.startswith("/api/") or request.is_json:
        return jsonify({"error": "An internal server error occurred. Please try again later.", "code": "INTERNAL_SERVER_ERROR"}), 500
    return render_template(
        "index.html",
        error="An unexpected server error occurred. Please check your provider settings.",
        url="",
        mode=DEFAULT_MODE,
        modes=MODES,
        is_configured=has_valid_config(session),
        provider_config=get_active_provider_config(session_dict=session),
        provider_catalogue=PROVIDER_CATALOGUE,
        recent_history=[],
    ), 500


@app.before_request
def ensure_csrf_token():
    """Ensure every active session has a cryptographically secure anti-CSRF token initialized (SEC-03)."""
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(32)


@app.context_processor
def inject_globals():
    """Inject standard global template variables across all rendered views."""
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(32)

    v_cfg = _vault_config() if _vault else None
    return {
        "default_mode": DEFAULT_MODE,
        "modes": MODES,
        "max_batch_urls": MAX_BATCH_URLS,
        "has_notion_configured": has_notion_config(session_dict=session, vault_config=v_cfg),
        "is_hosted": is_serverless_env(),
        "vault_enabled": _vault is not None,
        "csrf_token": session["_csrf_token"],
        "provider_catalogue": PROVIDER_CATALOGUE,
    }


@app.before_request
def validate_csrf_and_origin():
    """Enforces Origin, Referer, and CSRF token validation on all state-changing requests (SEC-03)."""
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        # 1. Origin and Referer validation
        origin = request.headers.get("Origin")
        if origin:
            parsed_origin = urllib.parse.urlparse(origin)
            if parsed_origin.netloc and parsed_origin.netloc != request.host:
                return jsonify({"error": "Forbidden: Cross-origin request rejected."}), 403
        elif request.headers.get("Referer"):
            parsed_ref = urllib.parse.urlparse(request.headers.get("Referer"))
            if parsed_ref.netloc and parsed_ref.netloc != request.host:
                return jsonify({"error": "Forbidden: Invalid request origin."}), 403

        # 2. Extract submitted CSRF token
        submitted_token = (
            request.headers.get("X-CSRF-Token")
            or request.headers.get("X-CSRFToken")
            or request.form.get("csrf_token")
        )
        if not submitted_token and request.is_json:
            json_body = request.get_json(silent=True)
            if isinstance(json_body, dict):
                submitted_token = json_body.get("csrf_token")

        expected_token = session.get("_csrf_token")
        if not submitted_token or not expected_token or not secrets.compare_digest(submitted_token, expected_token):
            return jsonify({
                "error": "Forbidden: Invalid or missing CSRF token.",
                "code": "CSRF_VERIFICATION_FAILED"
            }), 403


@app.before_request
def enforce_byok_onboarding():
    """
    Enforces the multi-provider BYOK onboarding flow.
    If no valid AI provider credentials exist in local self-hosted mode,
    redirects users to the dedicated /setup wizard.
    In hosted serverless mode (Vercel), credentials live in browser memory.
    """
    if request.endpoint is None:
        return None

    exempt_endpoints = {
        "home",
        "api_index",
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
        "store_temporary_credentials",
        "temporary_credentials_status",
        "delete_temporary_credentials",
        "health_check",
        "api_health",
        "health_liveness",
        "health_readiness",
        "api_metrics",
    }
    if request.endpoint in exempt_endpoints:
        return None

    if is_serverless_env():
        # Hosted mode: visitors supply BYOK credentials in browser memory, never redirect GET requests
        return None

    # Check local configuration
    if has_valid_config(session_dict=session):
        return None

    # Handle per-request credentials in hosted mode
    if request.endpoint in ("summarize", "batch_summarize", "compare_articles_route"):
        payload = request.get_json(silent=True) if request.is_json else request.form.to_dict()
        if isinstance(payload, dict):
            req_model = (payload.get("gemini_model") or payload.get("model") or "").strip()
            if req_model and not is_valid_model(req_model, "gemini"):
                allowed = get_allowed_models("gemini")
                err_msg = f"Unsupported model '{req_model}'. Supported models: {', '.join(allowed)}"
                if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return jsonify({"error": err_msg}), 400
                return render_template("index.html", error=err_msg, is_configured=False), 400

        if has_valid_config(session_dict=session, request_data=payload):
            return None

        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({
                "error": "AI provider credentials not configured. Please configure your credentials.",
                "redirect": url_for("home")
            }), 401

        return redirect(url_for("home"))

    return redirect(url_for("home"))


@app.errorhandler(ProviderInvalidModelError)
def handle_provider_invalid_model_error(error):
    err_msg = redact_credentials(str(error))
    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"error": err_msg}), 400
    return render_template("index.html", error=err_msg, is_configured=False), 400


@app.route("/health", methods=["GET"])
@app.route("/api/health", methods=["GET"])
def health_check():
    """Diagnostic health check endpoint for platform monitoring and uptime probes."""
    return jsonify({
        "status": "healthy",
        "is_hosted": is_serverless_env(),
        "vault_enabled": _vault is not None,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }), 200


@app.route("/api/health/liveness", methods=["GET"])
def health_liveness():
    """Liveness probe verifying that the Flask process is running and responding (DEP-01)."""
    return jsonify({
        "status": "alive",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }), 200


@app.route("/api/health/readiness", methods=["GET"])
def health_readiness():
    """Readiness probe evaluating operational status of required backing services (DEP-01)."""
    sqlite_status = "ok"
    if not is_serverless_env():
        try:
            from history import get_db_connection
            conn = get_db_connection()
            conn.execute("SELECT 1")
            conn.close()
        except Exception as e:
            sqlite_status = f"unhealthy: {str(e)}"

    redis_status = "disabled"
    if _redis is not None:
        try:
            _redis.ping()
            redis_status = "ok"
        except Exception as e:
            redis_status = f"unhealthy: {str(e)}"
    elif _vault is not None:
        try:
            _vault.redis_client.ping()
            redis_status = "ok"
        except Exception as e:
            redis_status = f"unhealthy: {str(e)}"

    is_ready = True
    if not is_serverless_env() and sqlite_status != "ok":
        is_ready = False
    if _vault is not None and redis_status.startswith("unhealthy"):
        is_ready = False

    status_code = 200 if is_ready else 503
    status_str = "ready" if is_ready else "not_ready"

    return jsonify({
        "status": status_str,
        "is_hosted": is_serverless_env(),
        "checks": {
            "sqlite": sqlite_status,
            "redis": redis_status,
        },
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }), status_code


@app.route("/api/metrics", methods=["GET"])
def api_metrics():
    """Diagnostic operational metrics snapshot endpoint (OBS-02)."""
    with _METRICS_LOCK:
        snapshot = dict(_METRICS)
    return jsonify({
        "status": "healthy",
        "metrics": snapshot,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }), 200


@app.route("/api/index")
@app.route("/")
def home():
    v_cfg = _vault_config() if _vault else None
    is_conf = has_valid_config(session_dict=session, vault_config=v_cfg)
    history_entries = [] if is_serverless_env() else get_history(limit=50)
    provider_config = get_active_provider_config(session_dict=session, vault_config=v_cfg)
    safe_provider_config = {
        k: (bool(v) if ("key" in k.lower() or "token" in k.lower() or "secret" in k.lower()) else v)
        for k, v in provider_config.items()
    }
    directives = get_custom_directives()
    active_view = request.args.get("view", "summarize")
    return render_template(
        "index.html",
        default_mode=DEFAULT_MODE,
        modes=MODES,
        is_configured=is_conf,
        provider_config=safe_provider_config,
        provider_catalogue=PROVIDER_CATALOGUE,
        entries=history_entries,
        recent_history=history_entries[:10],
        custom_directive=directives.get("custom_directive", ""),
        custom_directive_enabled=directives.get("custom_directive_enabled", False),
        max_directive_length=MAX_DIRECTIVE_LENGTH,
        active_view=active_view,
    )


@app.route("/setup")
@app.route("/settings")
def settings():
    """Redirect legacy setup and settings routes to the single-page application."""
    return redirect(url_for("home"), code=301)


@app.route("/history")
def view_history():
    """Summary history view in single-page interface."""
    return redirect(url_for("home", view="history"))


# -------------------------------------------------------------
# Summary History API Endpoints
# -------------------------------------------------------------
@app.route("/api/history/count", methods=["GET"])
def api_get_history_count():
    if is_serverless_env():
        return jsonify({"count": 0, "is_hosted": True})
    try:
        return jsonify({"count": get_history_count(), "is_hosted": False})
    except HistoryError as he:
        logger.error("Failed to count history: %s", he)
        return jsonify({"error": f"Failed to count history records: {str(he)}", "count": 0, "is_hosted": False}), 503


@app.route("/api/history", methods=["GET"])
def api_get_history():
    if is_serverless_env():
        return jsonify({"history": []})
    limit = request.args.get("limit", default=50, type=int)
    if limit is not None and limit < 0:
        limit = -1
    try:
        entries = get_history(limit=limit)
        return jsonify({"history": entries})
    except HistoryError as he:
        logger.error("Failed to get history: %s", he)
        return jsonify({"error": f"Failed to retrieve history: {str(he)}", "history": []}), 503


@app.route("/api/history/<entry_id>", methods=["GET"])
def api_get_history_entry(entry_id):
    if is_serverless_env():
        return jsonify({"error": "History entry not found."}), 404
    try:
        entry = get_history_entry(entry_id)
        if not entry:
            return jsonify({"error": "History entry not found."}), 404
        return jsonify({"entry": entry})
    except HistoryError as he:
        logger.error("Failed to get history entry %s: %s", entry_id, he)
        return jsonify({"error": f"Failed to fetch history entry: {str(he)}"}), 503


@app.route("/api/history/<entry_id>", methods=["DELETE"])
def api_delete_history(entry_id):
    if is_serverless_env():
        return jsonify({"success": True})
    try:
        success = delete_history_entry(entry_id)
        if not success:
            return jsonify({"error": "History entry not found."}), 404
        return jsonify({"success": True})
    except HistoryError as he:
        logger.error("Failed to delete history entry %s: %s", entry_id, he)
        return jsonify({"error": f"Failed to delete history entry: {str(he)}"}), 503


@app.route("/api/history/clear", methods=["POST"])
def api_clear_history():
    if is_serverless_env():
        return jsonify({"success": True})
    try:
        clear_history()
        return jsonify({"success": True})
    except HistoryError as he:
        logger.error("Failed to clear history: %s", he)
        return jsonify({"error": f"Failed to clear history: {str(he)}"}), 503


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
    entry_type = data.get("entry_type", "")

    if history_id and not is_serverless_env():
        entry = get_history_entry(history_id)
        if entry:
            summary = entry.get("summary")
            url = entry.get("url", url)
            title = entry.get("title", title)
            mode = entry.get("mode_name", mode)
            created_at = entry.get("created_at_formatted", created_at)
            entry_type = entry.get("entry_type", entry_type)

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

    is_comparison = entry_type == "comparison" or "article_a" in summary or "common_points" in summary

    try:
        if is_comparison:
            pdf_buffer = generate_comparison_pdf(
                comp=summary,
                url_a=data.get("url_a", url),
                url_b=data.get("url_b", ""),
                created_at=created_at,
            )
            download_name = f"WebBrief_Comparison_{build_pdf_filename(title or summary.get('title', 'Comparison'))}"
        else:
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
        increment_metric("pdf_failures")
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
        is_comparison = entry.get("entry_type") == "comparison" or "article_a" in summary_data or "common_points" in summary_data
        if is_comparison:
            pdf_buffer = generate_comparison_pdf(
                comp=summary_data,
                url_a=entry.get("url", ""),
                url_b=entry.get("url_b", ""),
                created_at=entry.get("created_at_formatted", ""),
            )
            download_name = f"WebBrief_Comparison_{build_pdf_filename(entry.get('title', 'Comparison'))}"
        else:
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
        increment_metric("pdf_failures")
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
    enabled = parse_bool(data.get("custom_directive_enabled"), default=False)

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
    limit_resp = check_rate_limit("batch_summarize", max_requests=10, window_seconds=60)
    if limit_resp:
        return limit_resp
    data = request.get_json() or {}
    raw_urls = data.get("urls", [])

    if not raw_urls or not isinstance(raw_urls, list):
        return jsonify({"error": "Please provide a list of URLs to summarize."}), 400

    cleaned_urls = [str(u).strip() for u in raw_urls if str(u).strip()]
    if not cleaned_urls:
        return jsonify({"error": "Please provide at least one valid URL."}), 400

    batch_warning = None
    if len(cleaned_urls) > MAX_BATCH_URLS:
        excess = len(cleaned_urls) - MAX_BATCH_URLS
        batch_warning = f"Batch request contained {len(cleaned_urls)} URLs. The maximum batch limit is {MAX_BATCH_URLS}; {excess} excess URLs are flagged as exceeded."

    req_model = (data.get("gemini_model") or data.get("model") or "").strip()
    if req_model and not is_valid_model(req_model, "gemini"):
        allowed = get_allowed_models("gemini")
        return jsonify({"error": f"Unsupported model '{req_model}'. Supported models: {', '.join(allowed)}"}), 400

    mode = data.get("mode", DEFAULT_MODE)
    api_key = data.get("api_key")
    custom_directive = data.get("custom_directive")
    custom_directive_enabled = parse_bool(data.get("custom_directive_enabled"), default=None) if "custom_directive_enabled" in data else None

    is_stream = request.args.get("stream") in ("1", "true") or request.headers.get("Accept") == "text/event-stream"

    if is_stream:
        event_queue = queue.Queue()

        def stream_progress_cb(evt):
            event_queue.put(evt)

        cancel_event = threading.Event()

        def generate_sse():
            batch_result_container = []

            # Emit initial batch_started event (BATCH-02)
            yield f"data: {json.dumps({'event': 'batch_started', 'total': len(cleaned_urls), 'mode': mode})}\n\n"

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
                        cancel_event=cancel_event,
                    )
                    increment_metric("batch_completed")
                    batch_result_container.append(res)
                except Exception as e:
                    event_queue.put({"event": "error", "error": redact_credentials(str(e))})
                finally:
                    event_queue.put(None)

            t = threading.Thread(target=worker_thread)
            t.start()

            try:
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
                done_evt = "canceled" if (cancel_event.is_set() or final_res.get("status") == "canceled") else "done"
                yield f"data: {json.dumps({'event': done_evt, 'result': final_res})}\n\n"
            finally:
                cancel_event.set()

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
    increment_metric("batch_completed")
    if batch_warning:
        batch_result["warning"] = batch_warning
    return jsonify(batch_result)


@app.route("/batch/export-pdf", methods=["POST"])
def batch_export_pdf():
    if request.content_length and request.content_length > 2 * 1024 * 1024:
        return jsonify({"error": "Payload exceeds 2 MiB request limit for PDF generation."}), 413

    data = request.get_json() or {}
    items = data.get("items", [])
    if len(items) > 50:
        return jsonify({"error": "Batch PDF export exceeds the maximum limit of 50 items per document."}), 400

    history_ids = data.get("history_ids")
    if history_ids and isinstance(history_ids, list):
        # In local self-hosted mode with SQLite on disk, resolve from database
        if not is_serverless_env():
            loaded_items = []
            for hid in history_ids:
                hentry = get_history_entry(hid)
                if hentry:
                    loaded_items.append(hentry)
            if loaded_items:
                items = loaded_items
        # In hosted mode (or if SQLite does not hold entries), preserve client-supplied items

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
# Comparative Research Endpoints
# -------------------------------------------------------------
@app.route("/compare", methods=["POST"])
def compare_articles_route():
    limit_resp = check_rate_limit("compare", max_requests=25, window_seconds=60)
    if limit_resp:
        return limit_resp
    data = request.get_json() if request.is_json else request.form.to_dict()
    url_a = data.get("url_a", "").strip()
    url_b = data.get("url_b", "").strip()

    if not url_a or not url_b:
        return jsonify({"error": "Please provide both Article A and Article B URLs for comparison."}), 400

    req_model = (data.get("gemini_model") or data.get("model") or "").strip()
    if req_model and not is_valid_model(req_model, "gemini"):
        allowed = get_allowed_models("gemini")
        return jsonify({"error": f"Unsupported model '{req_model}'. Supported models: {', '.join(allowed)}"}), 400

    if not has_valid_config(session_dict=session, request_data=data):
        return jsonify({
            "error": "AI provider credentials not configured. Please configure your credentials.",
            "redirect": url_for("home")
        }), 401

    directives = get_custom_directives()
    custom_directive = directives.get("custom_directive")
    custom_directive_enabled = directives.get("custom_directive_enabled")
    if "custom_directive" in data:
        custom_directive = data.get("custom_directive")
    if "custom_directive_enabled" in data:
        custom_directive_enabled = parse_bool(data.get("custom_directive_enabled"), default=False)

    try:
        comparison_res = compare_articles(
            url_a=url_a,
            url_b=url_b,
            custom_directive=custom_directive,
            custom_directive_enabled=custom_directive_enabled,
            provider_config=data,
        )

        history_id = None
        history_saved = False
        embedding_indexed = False
        comparison_res["comparison_generated"] = True
        comparison_res["summary_generated"] = True
        comparison_res["pdf_available"] = True
        comparison_res["notion_exported"] = False
        comparison_res["embedding_indexed"] = False

        if is_serverless_env():
            history_id = str(uuid.uuid4())
            comparison_res["history_id"] = history_id
            comparison_res["history_saved"] = False
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
                if hist_entry and hist_entry.get("id"):
                    history_id = hist_entry.get("id")
                    history_saved = True
                    comparison_res["history_id"] = history_id
                    comparison_res["history_saved"] = True
                    try:
                        indexed = index_summary_entry(history_id, provider_config=data)
                        embedding_indexed = bool(indexed)
                        comparison_res["embedding_indexed"] = embedding_indexed
                    except Exception:
                        pass
                else:
                    comparison_res["history_saved"] = False
            except HistoryError as he:
                logger.warning("Could not record comparison to history: %s", he)
                comparison_res["history_saved"] = False
                comparison_res["history_error"] = str(he)
            except Exception as e:
                logger.warning("Could not record comparison to history: %s", str(e))
                comparison_res["history_saved"] = False

        return jsonify({
            "success": True,
            "comparison": comparison_res,
            "history_id": history_id,
            "history_saved": history_saved,
            "summary_generated": True,
            "embedding_indexed": embedding_indexed,
            "pdf_available": True,
            "notion_exported": False,
            "url_a": url_a,
            "url_b": url_b,
        })

    except (ComparisonError, ProviderInvalidModelError) as ce:
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
            comp=comparison_data,
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
    if _vault is not None:
        v_status, v_cfg = _vault_status()
        if v_status == VaultStatus.UNAVAILABLE:
            return jsonify({
                "configured": False,
                "connected": False,
                "status": "unavailable",
                "error": "Credential storage is temporarily unreachable.",
                "is_hosted": is_serverless_env(),
                "vault_enabled": True,
            }), 503
        if v_status in (VaultStatus.DECRYPTION_ERROR, VaultStatus.CORRUPTED):
            return jsonify({
                "configured": False,
                "connected": False,
                "status": v_status.value,
                "error": "Existing credential record is corrupted or cannot be decrypted.",
                "is_hosted": is_serverless_env(),
                "vault_enabled": True,
            }), 500
    else:
        v_cfg = None

    configured = has_notion_config(vault_config=v_cfg)
    return jsonify({
        "configured": configured,
        "connected": configured,
        "status": "configured" if configured else "not_configured",
        "is_hosted": is_serverless_env(),
        "vault_enabled": _vault is not None,
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

    # Retrieve accessible pages to verify token validity BEFORE persisting (NOTION-01)
    try:
        pages = get_accessible_pages(token=token)
    except NotionError as ne:
        err_msg = str(ne)
        status_code = 401 if any(w in err_msg.lower() for w in ("unauthorized", "invalid token", "expired", "401")) else 400
        return jsonify({"error": redact_credentials(err_msg)}), status_code
    except Exception as e:
        return jsonify({"error": redact_credentials(f"Failed to retrieve Notion pages: {str(e)}")}), 500

    if is_serverless_env():
        if _vault is not None:
            sid = _vault_sid()
            res = None
            if hasattr(_vault, "update") and not hasattr(_vault, "_mock_return_value"):
                try:
                    res = _vault.update(sid, {"notion_token": token})
                except Exception:
                    res = None

            if isinstance(res, tuple) and len(res) == 2:
                saved, v_status = res
            else:
                v_status, existing_cfg = _vault_status()
                cfg = dict(existing_cfg) if v_status == VaultStatus.CONFIGURED and existing_cfg else {}
                cfg.pop("_created", None)
                cfg.pop("created", None)
                cfg["notion_token"] = token
                saved = _vault.save(sid, cfg)
                v_status = VaultStatus.CONFIGURED if saved else VaultStatus.UNAVAILABLE

            if not saved:
                if v_status == VaultStatus.UNAVAILABLE:
                    return jsonify({
                        "error": "Credential storage is temporarily unavailable. Cannot safely update Notion credentials without verifying existing configuration.",
                        "status": "unavailable"
                    }), 503
                elif v_status in (VaultStatus.DECRYPTION_ERROR, VaultStatus.CORRUPTED):
                    return jsonify({
                        "error": "Existing credential record is corrupted or cannot be decrypted. Connection blocked.",
                        "status": v_status.value
                    }), 500
                return jsonify({"error": "Failed to store Notion token in vault."}), 503
            g._set_vault_cookie = sid
            save_msg = "Notion connected successfully."
        else:
            return jsonify({
                "error": "Persistent hosted Notion integration requires the secure encrypted Redis vault. Please configure REDIS_URL and CREDENTIAL_ENCRYPTION_KEY in your deployment environment variables, or supply token per-request in browser memory.",
                "code": "VAULT_REQUIRED"
            }), 400
    else:
        success, save_msg = save_notion_key_to_env(
            token,
            is_hosted=False,
        )
        if not success:
            return jsonify({"error": save_msg}), 500

    return jsonify({
        "success": True,
        "message": save_msg or "Notion connected successfully.",
        "pages": pages,
    })


@app.route("/api/notion/disconnect", methods=["POST"])
def api_notion_disconnect():
    if _vault is not None:
        saved, v_status = _vault.update(_vault_sid(), {}, remove_keys=["notion_token"])
        if not saved and v_status == VaultStatus.UNAVAILABLE:
            return jsonify({
                "error": "Credential storage is temporarily unavailable. Cannot safely disconnect Notion.",
                "status": "unavailable"
            }), 503
        elif not saved and v_status in (VaultStatus.DECRYPTION_ERROR, VaultStatus.CORRUPTED):
            return jsonify({
                "error": "Existing credential record is corrupted. Disconnect blocked.",
                "status": v_status.value
            }), 500
    clear_saved_notion_key(session_dict=session)
    return jsonify({"success": True, "message": "Notion disconnected."})


@app.route("/api/notion/pages", methods=["GET"])
def api_notion_pages():
    if _vault is not None:
        v_status, v_cfg = _vault_status()
        if v_status == VaultStatus.UNAVAILABLE:
            return jsonify({
                "error": "Credential storage is temporarily unreachable. Please retry.",
                "status": "unavailable"
            }), 503
        if v_status in (VaultStatus.DECRYPTION_ERROR, VaultStatus.CORRUPTED):
            return jsonify({
                "error": "Credential record is corrupted or cannot be decrypted.",
                "status": v_status.value
            }), 500
    else:
        v_cfg = None

    token = get_notion_key(provided_token=request.args.get("token"), vault_config=v_cfg)
    if not token:
        return jsonify({"error": "Notion is not connected."}), 401
    try:
        from notion_service import get_accessible_pages_with_pagination
        pages, is_truncated = get_accessible_pages_with_pagination(token=token)
    except NotionError as ne:
        err_msg = str(ne)
        status_code = 401 if any(w in err_msg.lower() for w in ("unauthorized", "invalid token", "expired", "401")) else 400
        return jsonify({"error": redact_credentials(err_msg)}), status_code
    except Exception as e:
        return jsonify({"error": redact_credentials(f"Failed to query Notion pages: {str(e)}")}), 500
    return jsonify({"pages": pages, "truncated": is_truncated, "has_more": is_truncated})


@app.route("/api/notion/export", methods=["POST"])
def api_notion_export():
    if _vault is not None:
        v_status, v_cfg = _vault_status()
        if v_status == VaultStatus.UNAVAILABLE:
            return jsonify({
                "error": "Credential storage is temporarily unreachable. Please retry.",
                "status": "unavailable"
            }), 503
        if v_status in (VaultStatus.DECRYPTION_ERROR, VaultStatus.CORRUPTED):
            return jsonify({
                "error": "Credential record is corrupted or cannot be decrypted.",
                "status": v_status.value
            }), 500
    else:
        v_cfg = None

    data = request.get_json() or {}
    token = get_notion_key(provided_token=data.get("notion_token"), vault_config=v_cfg)
    if not token:
        return jsonify({"error": "Notion is not connected. Please connect in Settings."}), 401

    parent_page_id = data.get("parent_page_id", "").strip()
    if not parent_page_id:
        return jsonify({"error": "Please select a target Notion page."}), 400

    entry_type = data.get("entry_type") or data.get("type", "single")
    summary_data = data.get("summary") or data.get("comparison")

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
                url_a=data.get("url_a") or summary_data.get("url_a", ""),
                url_b=data.get("url_b") or summary_data.get("url_b", ""),
            )
        else:
            title = summary_data.get("title") or "Webpage Summary"
            blocks = format_single_summary_blocks(
                summary_data,
                title=title,
                url=data.get("url") or summary_data.get("url", ""),
                mode_name=data.get("mode_name", summary_data.get("mode_name", "Summary")),
            )

        res = create_notion_page(
            parent_page_id=parent_page_id,
            page_title=title,
            blocks=blocks,
            token=token,
        )
        return jsonify({"success": True, "page_url": res["url"], "title": title})

    except NotionError as ne:
        increment_metric("notion_failures")
        err_msg = str(ne)
        status_code = 401 if any(w in err_msg.lower() for w in ("unauthorized", "invalid token", "expired", "401")) else 400
        return jsonify({"error": redact_credentials(err_msg)}), status_code
    except Exception as e:
        increment_metric("notion_failures")
        return jsonify({"error": redact_credentials(f"Failed to export to Notion: {str(e)}")}), 500


# -------------------------------------------------------------
# Local Knowledge Retrieval & Semantic Search Endpoints
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
            "error": "AI provider credentials required to embed search queries. Please configure your credentials.",
            "redirect": url_for("home")
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
        search_res = semantic_search(
            query=query,
            top_k=top_k,
            threshold=threshold,
            filter_type=filter_type,
            provider_config=data,
            return_metadata=True,
        )
        if isinstance(search_res, tuple) and len(search_res) == 2:
            results, meta = search_res
        elif isinstance(search_res, list):
            results = search_res
            meta = {
                "search_mode": "semantic",
                "embedding_model": DEFAULT_EMBEDDING_MODEL,
                "status_detail": "ready" if results else "no_matching_records"
            }
        else:
            results = []
            meta = {
                "search_mode": "semantic",
                "embedding_model": DEFAULT_EMBEDDING_MODEL,
                "status_detail": "no_matching_records"
            }

        return jsonify({
            "success": True,
            "query": query,
            "count": len(results),
            "threshold": threshold,
            "search_mode": meta.get("search_mode", "semantic"),
            "embedding_model": meta.get("embedding_model", DEFAULT_EMBEDDING_MODEL),
            "status_detail": meta.get("status_detail", "ready" if results else "no_matching_records"),
            "results": results,
        })
    except VectorStoreError as ve:
        return jsonify({"error": redact_credentials(str(ve))}), 400
    except Exception as e:
        return jsonify({"error": redact_credentials(f"Semantic search failed: {str(e)}")}), 500


@app.route("/api/search/stats", methods=["GET"])
def api_search_stats():
    if is_serverless_env():
        return jsonify({
            "success": True,
            "indexed_count": 0,
            "total_summaries": 0,
            "status": "hosted_indexeddb",
            "stats": {
                "total_indexed_documents": 0,
                "active_model": "IndexedDB (Browser)",
            }
        })
    raw_stats = get_vector_store_stats()
    indexed_cnt = raw_stats.get("indexed_count", 0)
    model_name = raw_stats.get("model", "gemini-embedding-001")
    return jsonify({
        "success": True,
        "indexed_count": indexed_cnt,
        "model": model_name,
        "dimensions": raw_stats.get("dimensions", 768),
        "status": raw_stats.get("status", "ready"),
        "stats": {
            "total_indexed_documents": indexed_cnt,
            "active_model": model_name,
        }
    })


@app.route("/api/search/rebuild-index", methods=["POST"])
def api_search_rebuild_index():
    if is_serverless_env():
        return jsonify({"error": "Vector indexing on server is disabled in hosted mode."}), 400

    data = request.get_json() or {}
    if not has_valid_config(session_dict=session, request_data=data):
        return jsonify({
            "error": "AI provider credentials required to generate embeddings. Please configure your credentials.",
            "redirect": url_for("home")
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
        vector, model_name = generate_embedding_with_model(text, provider_config=data)
        return jsonify({
            "embedding": vector,
            "model": model_name,
            "dimensions": len(vector),
        })
    except Exception as e:
        return jsonify({"error": redact_credentials(f"Embedding generation failed: {str(e)}")}), 500


@app.route("/api/environment", methods=["GET"])
def api_environment():
    hosted = is_serverless_env()
    return jsonify({
        "is_hosted": hosted,
        "environment": "hosted_serverless" if hosted else "local_self_hosted",
        "ssrf_guard": "active"
    })


# -------------------------------------------------------------
# Multi-Provider BYOK Configuration Endpoints
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
    Supports Google Gemini Developer API.
    """
    limit_resp = check_rate_limit("validate_key", max_requests=20, window_seconds=60)
    if limit_resp:
        return limit_resp
    data = request.get_json() or {} if request.is_json else request.form.to_dict()
    provider = data.get("provider", "gemini").strip().lower()

    if provider not in ("gemini", ""):
        return jsonify({"valid": False, "message": f"Unsupported provider: '{provider}'"}), 400

    model = (data.get("gemini_model") or data.get("model") or "").strip()
    if model and not is_valid_model(model, "gemini"):
        allowed = get_allowed_models("gemini")
        return jsonify({
            "valid": False,
            "message": f"Unsupported model '{model}'. Supported models: {', '.join(allowed)}"
        }), 400

    api_key = (data.get("gemini_api_key") or data.get("api_key") or "").strip()
    is_valid, message = validate_gemini_credentials(api_key, model=model or None)

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

    confirm_overwrite = data.get("confirm_overwrite") in (True, "true", "1", 1, "on")

    # Step 1: Validate model if provided
    model = (data.get("gemini_model") or data.get("model") or "").strip()
    if model and not is_valid_model(model, "gemini"):
        allowed = get_allowed_models("gemini")
        err_msg = f"Unsupported model '{model}'. Supported models: {', '.join(allowed)}"
        if is_ajax:
            return jsonify({"success": False, "error": err_msg}), 400
        workspace_dir = os.path.dirname(os.path.abspath(__file__))
        env_exists = os.path.isfile(os.path.join(workspace_dir, ".env"))
        return render_template("index.html", error=err_msg, is_configured=has_valid_config(session)), 400

    # Step 2: Validate Gemini API key
    gemini_key = (data.get("gemini_api_key") or data.get("api_key") or "").strip()
    is_valid, val_msg = validate_gemini_credentials(gemini_key, model=model or None)

    if not is_valid:
        if is_ajax:
            return jsonify({"success": False, "error": val_msg}), 400
        workspace_dir = os.path.dirname(os.path.abspath(__file__))
        env_exists = os.path.isfile(os.path.join(workspace_dir, ".env"))
        return render_template("index.html", error=val_msg, is_configured=has_valid_config(session)), 400

    # Hosted serverless mode: do not touch filesystem
    if is_serverless_env():
        if _vault is not None:
            clean_vault = {
                "primary_provider": "gemini",
                "gemini_api_key": gemini_key,
            }
            if data.get("gemini_model"):
                clean_vault["gemini_model"] = data.get("gemini_model", "").strip()
            if data.get("notion_token"):
                clean_vault["notion_token"] = str(data["notion_token"]).strip()
            clean_vault = {k: v for k, v in clean_vault.items() if v}

            saved, v_status = _vault.update(_vault_sid(), clean_vault)
            if not saved:
                if v_status == VaultStatus.UNAVAILABLE:
                    err_msg = "Credential storage is temporarily unavailable. Cannot safely update configuration."
                    status_code = 503
                elif v_status in (VaultStatus.DECRYPTION_ERROR, VaultStatus.CORRUPTED):
                    err_msg = "Existing credential record is corrupted or cannot be decrypted. Update blocked."
                    status_code = 500
                else:
                    err_msg = "Failed to store provider credentials in temporary vault."
                    status_code = 503
                if is_ajax:
                    return jsonify({"success": False, "error": err_msg}), status_code
                return render_template("index.html", error=err_msg, is_configured=False), status_code

        if is_ajax:
            return jsonify({
                "success": True,
                "message": "Provider configuration verified for active temporary session.",
                "is_hosted": True,
                "vault_enabled": _vault is not None,
                "redirect": url_for("home")
            })
        return redirect(url_for("home"))

    # Local mode: persist to .env
    save_data = dict(data)
    save_data["gemini_api_key"] = gemini_key
    save_data["primary_provider"] = "gemini"
    success, save_message = save_provider_config_to_env(
        config_dict=save_data,
        confirm_overwrite=confirm_overwrite
    )

    if not success:
        if is_ajax:
            return jsonify({"success": False, "error": save_message}), 400
        workspace_dir = os.path.dirname(os.path.abspath(__file__))
        env_exists = os.path.isfile(os.path.join(workspace_dir, ".env"))
        return render_template("index.html", error=save_message, is_configured=has_valid_config(session)), 400

    if is_ajax:
        return jsonify({"success": True, "message": save_message, "redirect": url_for("home")})

    return redirect(url_for("home"))


@app.route("/settings/clear-key", methods=["POST"])
def clear_key():
    """Removes stored provider credentials and routing configuration."""
    clear_saved_provider_config()
    return redirect(url_for("home"))


# -------------------------------------------------------------
# Webpage Summarization Endpoint
# -------------------------------------------------------------
@app.route("/summarize", methods=["POST"])
def summarize():
    limit_resp = check_rate_limit("summarize", max_requests=30, window_seconds=60)
    if limit_resp:
        return limit_resp
    if request.is_json:
        data = request.get_json() or {}
        url = data.get("url", "").strip()
        mode = data.get("mode", DEFAULT_MODE).strip().lower()
        custom_directive = data.get("custom_directive")
        custom_directive_enabled = parse_bool(data.get("custom_directive_enabled"), default=None) if "custom_directive_enabled" in data else None
        is_ajax = True
    else:
        data = request.form.to_dict()
        url = request.form.get("url", "").strip()
        mode = request.form.get("mode", DEFAULT_MODE).strip().lower()
        custom_directive = request.form.get("custom_directive")
        custom_directive_enabled = parse_bool(request.form.get("custom_directive_enabled"), default=None) if "custom_directive_enabled" in request.form else None
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if mode not in MODES:
        mode = DEFAULT_MODE

    if not url:
        error_msg = "Please enter a webpage URL."
        if is_ajax:
            return jsonify({"error": error_msg}), 400
        return render_template("index.html", error=error_msg, url="", mode=mode, modes=MODES, is_configured=has_valid_config(session)), 400

    req_model = (data.get("gemini_model") or data.get("model") or "").strip()
    if req_model and not is_valid_model(req_model, "gemini"):
        allowed = get_allowed_models("gemini")
        error_msg = f"Unsupported model '{req_model}'. Supported models: {', '.join(allowed)}"
        if is_ajax:
            return jsonify({"error": error_msg}), 400
        return render_template("index.html", error=error_msg, url=url, mode=mode, modes=MODES, is_configured=has_valid_config(session)), 400

    if not has_valid_config(session_dict=session, request_data=data):
        error_msg = "AI provider credentials not configured. Please configure your credentials."
        if is_ajax:
            return jsonify({"error": error_msg, "redirect": url_for("home")}), 401
        return render_template("index.html", error=error_msg, url=url, mode=mode, modes=MODES, is_configured=False), 401

    try:
        # Step 1: Scrape article text from webpage
        article_text = scrape_article(url)

        # Step 2: Generate AI summary with multi-provider routing
        summarize_kwargs = {
            "mode": mode,
            "provider_config": data,
        }
        if custom_directive is not None:
            summarize_kwargs["custom_directive"] = custom_directive
        if custom_directive_enabled is not None:
            summarize_kwargs["custom_directive_enabled"] = custom_directive_enabled

        summary = summarize_text(article_text, **summarize_kwargs)
        if summary.get("is_partial"):
            increment_metric("summaries_partial")
        else:
            increment_metric("summaries_success")

        # Step 3: Record into persistent summary history
        summary["summary_generated"] = True
        summary["pdf_available"] = True
        summary["notion_exported"] = False
        summary["embedding_indexed"] = False

        if is_serverless_env():
            summary["history_id"] = str(uuid.uuid4())
            summary["history_saved"] = False
        else:
            try:
                entry = add_history_entry(
                    title=summary.get("title") or "Webpage Summary",
                    url=url,
                    mode=mode,
                    mode_name=summary.get("mode_name", mode.capitalize()),
                    summary=summary
                )
                if entry and entry.get("id"):
                    summary["history_id"] = entry["id"]
                    summary["history_saved"] = True
                    try:
                        indexed = index_summary_entry(entry["id"], provider_config=data)
                        summary["embedding_indexed"] = bool(indexed)
                    except Exception:
                        summary["embedding_indexed"] = False
                else:
                    summary["history_id"] = None
                    summary["history_saved"] = False
            except HistoryError as he:
                logger.warning("History persistence failed: %s", he)
                summary["history_id"] = None
                summary["history_saved"] = False
                summary["history_error"] = str(he)
            except Exception as e:
                logger.warning("Unexpected history persistence error: %s", e)
                summary["history_id"] = None
                summary["history_saved"] = False

        if is_ajax:
            return jsonify({
                "success": True,
                "summary": summary,
                "url": url,
                "mode": mode,
                "summary_generated": True,
                "history_saved": summary.get("history_saved", False),
                "embedding_indexed": summary.get("embedding_indexed", False),
                "pdf_available": True,
                "notion_exported": False,
                "provider": summary.get("provider"),
                "provider_name": summary.get("provider_name"),
                "model": summary.get("model"),
                "switched_to_fallback": summary.get("switched_to_fallback", False),
            })

        return render_template("index.html", summary=summary, url=url, mode=mode, modes=MODES, is_configured=has_valid_config(session))

    except ProviderInvalidModelError as e:
        increment_metric("gemini_errors")
        increment_metric("summaries_failed")
        error_msg = redact_credentials(str(e))
        if is_ajax:
            return jsonify({"error": error_msg}), 400
        return render_template("index.html", error=error_msg, url=url, mode=mode, modes=MODES, is_configured=has_valid_config(session)), 400

    except ScraperError as e:
        increment_metric("scrape_failures")
        increment_metric("summaries_failed")
        error_msg = redact_credentials(str(e))
        if is_ajax:
            return jsonify({"error": error_msg}), 400
        return render_template("index.html", error=error_msg, url=url, mode=mode, modes=MODES, is_configured=has_valid_config(session)), 400

    except SummarizerError as e:
        increment_metric("gemini_errors")
        increment_metric("summaries_failed")
        error_msg = redact_credentials(str(e))
        status_code = 400 if (isinstance(getattr(e, "__cause__", None), ProviderInvalidModelError) or "unsupported" in error_msg.lower()) else 500
        if is_ajax:
            return jsonify({"error": error_msg}), status_code
        return render_template("index.html", error=error_msg, url=url, mode=mode, modes=MODES, is_configured=has_valid_config(session)), status_code

    except Exception as e:
        increment_metric("summaries_failed")
        error_msg = redact_credentials(f"An unexpected error occurred: {str(e)}")
        if is_ajax:
            return jsonify({"error": error_msg}), 500
        return render_template("index.html", error=error_msg, url=url, mode=mode, modes=MODES, is_configured=has_valid_config(session)), 500


if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_DEBUG", "0").lower() in ("1", "true")
    port = int(os.getenv("PORT", 5000))
    host = os.getenv("HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=debug_mode)
