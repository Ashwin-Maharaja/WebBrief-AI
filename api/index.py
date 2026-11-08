"""
Vercel Serverless Function entrypoint for WebBrief AI (v1.6.2).
Exports the Flask application instance 'app' for serverless hosting on Vercel.
Includes a Flask-native error boundary to diagnose any cold-start bootstrap failures.
"""
import sys
import os
import logging

# Ensure root directory is on sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

logger = logging.getLogger(__name__)

try:
    from app import app
    # Explicitly ensure template and static folders resolve to project root on Vercel
    template_dir = os.path.join(BASE_DIR, "templates")
    static_dir = os.path.join(BASE_DIR, "static")
    if os.path.isdir(template_dir):
        app.template_folder = template_dir
    if os.path.isdir(static_dir):
        app.static_folder = static_dir

except Exception as exc:
    logger.critical("Failed to bootstrap WebBrief AI application: %s", str(exc), exc_info=True)
    from flask import Flask, jsonify

    fallback_app = Flask(__name__)

    @fallback_app.route("/<path:path>", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    @fallback_app.route("/", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    def bootstrap_error(path=""):
        return jsonify({
            "status": "error",
            "code": "BOOTSTRAP_ERROR",
            "message": "WebBrief AI failed to initialize due to a serverless bootstrap error.",
            "detail": "Check deployment runtime logs.",
        }), 500

    app = fallback_app

handler = app
