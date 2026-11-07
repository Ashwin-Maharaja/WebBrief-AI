"""
Vercel Serverless Function entrypoint for WebBrief AI.
Exports the WSGI Flask application callable for serverless hosting.
"""
import sys
import os

# Ensure the root project directory is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app

# Expose both app and handler for universal Vercel Python runtime compatibility
handler = app
