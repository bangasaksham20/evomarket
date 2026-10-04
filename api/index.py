"""Vercel serverless entrypoint.

Vercel's Python runtime executes the ASGI `app` exported here natively
(no uvicorn needed) and discovers the concrete `/api/*` routes from it.

Local development is unchanged:
    uvicorn server:app --host 0.0.0.0 --port 8080
"""
import os
import sys

# Make the project root importable so we can reuse server.py as-is.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import app  # noqa: F401  (Vercel looks for the `app` variable)
