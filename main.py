"""Entrypoint for hosts that look for a top-level `app` (Vercel, `uvicorn main:app`)."""

from finley.app import create_app

app = create_app()
