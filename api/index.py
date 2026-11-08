"""
Vercel Serverless Function entrypoint for WebBrief AI (v1.6.1).
Exports the WSGI Flask application callable for serverless hosting.
Includes error-boundary fallback to gracefully report configuration or bootstrap failures.
"""
import sys
import os
import json
import logging

# Ensure the root project directory is on sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

logger = logging.getLogger(__name__)

try:
    from app import app
except Exception as exc:
    logger.critical("Failed to bootstrap WebBrief AI application: %s", str(exc), exc_info=True)

    def app(environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path in ("/health", "/api/health"):
            status = "500 Internal Server Error"
            body = json.dumps({
                "status": "degraded",
                "error": "Application bootstrap failed. Missing required environment configuration (e.g. FLASK_SECRET_KEY).",
                "detail": str(exc)
            }).encode("utf-8")
            content_type = "application/json"
        else:
            status = "500 Internal Server Error"
            body = (
                b"<!DOCTYPE html><html><head><title>WebBrief AI - Configuration Error</title></head>"
                b"<body style='font-family:system-ui,-apple-system,sans-serif;padding:2.5rem;background:#0f172a;color:#f8fafc;max-width:700px;margin:auto;'>"
                b"<h2 style='color:#ef4444;'>WebBrief AI &mdash; Serverless Configuration Error</h2>"
                b"<p>The application could not start due to missing environment configuration:</p>"
                b"<pre style='background:#1e293b;padding:1rem;border-radius:8px;border:1px solid #334155;overflow:auto;'>" + str(exc).encode("utf-8") + b"</pre>"
                b"<p style='color:#94a3b8;'>If running on Vercel, please ensure <code>FLASK_SECRET_KEY</code> is configured in your Vercel Project Environment Variables.</p>"
                b"</body></html>"
            )
            content_type = "text/html"

        start_response(status, [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body)))
        ])
        return [body]

handler = app
