"""
WebBrief AI — Vercel Serverless Function Entrypoint & Diagnostic Health Gate.
Exports the WSGI Flask application callable for serverless hosting.
Catches module-level import errors and provides diagnostic health check & traceback rendering.
"""
import sys
import os
import traceback
import json

# Ensure project root is on sys.path
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

# Set serverless environment marker
os.environ["VERCEL"] = "1"
os.environ["WEBBRIEF_HOSTED"] = "1"

_import_error = None
flask_app = None

try:
    from app import app as flask_app
except Exception as exc:
    _import_error = traceback.format_exc()
    print("FATAL APP IMPORT ERROR ON VERCEL:\n" + _import_error, file=sys.stderr)


def application(environ, start_response):
    """Universal WSGI entrypoint with built-in health check and error boundary."""
    path_info = environ.get("PATH_INFO", "")
    
    # Direct serverless health check endpoint
    if path_info in ("/health", "/api/health", "/api/v1/health"):
        if _import_error is not None:
            status = "500 Internal Server Error"
            headers = [("Content-Type", "application/json")]
            start_response(status, headers)
            return [json.dumps({
                "status": "unhealthy",
                "stage": "import",
                "error": str(_import_error)
            }, indent=2).encode("utf-8")]
        
        status = "200 OK"
        headers = [("Content-Type", "application/json")]
        start_response(status, headers)
        return [json.dumps({
            "status": "healthy",
            "version": "1.7.0",
            "platform": "vercel_serverless",
            "python": sys.version,
            "root_dir": root_dir
        }, indent=2).encode("utf-8")]

    # If application failed to import, return readable HTML diagnostics rather than 500 FUNCTION_INVOCATION_FAILED
    if _import_error is not None:
        status = "500 Internal Server Error"
        headers = [("Content-Type", "text/html; charset=utf-8")]
        start_response(status, headers)
        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>WebBrief AI — Initialization Error</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 2rem; line-height: 1.5; }}
        .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 2rem; max-width: 900px; margin: 0 auto; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }}
        h1 {{ color: #f43f5e; margin-top: 0; font-size: 1.5rem; }}
        pre {{ background: #020617; border: 1px solid #334155; padding: 1.25rem; border-radius: 8px; color: #fca5a5; overflow-x: auto; font-size: 0.9rem; }}
        .badge {{ display: inline-block; background: #38bdf8; color: #020617; padding: 0.25rem 0.5rem; border-radius: 4px; font-size: 0.75rem; font-weight: bold; margin-bottom: 1rem; }}
    </style>
</head>
<body>
    <div class="card">
        <span class="badge">WebBrief AI v1.7.0 Diagnostic Guard</span>
        <h1>Application Startup Failure on Serverless Host</h1>
        <p>The Python backend encountered an unhandled exception during module initialization:</p>
        <pre>{_import_error}</pre>
    </div>
</body>
</html>"""
        return [html.encode("utf-8")]

    # Forward to Flask WSGI application with error boundary
    try:
        return flask_app(environ, start_response)
    except Exception as exc:
        exc_trace = traceback.format_exc()
        print("UNHANDLED WSGI EXECUTION ERROR:\n" + exc_trace, file=sys.stderr)
        status = "500 Internal Server Error"
        headers = [("Content-Type", "text/html; charset=utf-8")]
        start_response(status, headers)
        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>WebBrief AI — Runtime Error</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 2rem; line-height: 1.5; }}
        .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 2rem; max-width: 900px; margin: 0 auto; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }}
        h1 {{ color: #f43f5e; margin-top: 0; font-size: 1.5rem; }}
        pre {{ background: #020617; border: 1px solid #334155; padding: 1.25rem; border-radius: 8px; color: #fca5a5; overflow-x: auto; font-size: 0.9rem; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>Unhandled Request Runtime Error</h1>
        <p>A fatal exception occurred during request execution:</p>
        <pre>{exc_trace}</pre>
    </div>
</body>
</html>"""
        return [html.encode("utf-8")]

# Expose app, application, and handler callables for all Vercel runtime variants
app = application
application = application
handler = application
