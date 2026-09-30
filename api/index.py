"""
Vercel Serverless ASGI Entrypoint for CIP — Crisis Intelligence Platform.
Re-exports the existing FastAPI application from src.api.main without modifying architecture.
"""

import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from src.api.main import app

__all__ = ["app"]
