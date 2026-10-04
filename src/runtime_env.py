"""
Runtime environment detection for CIP.

Single source of truth for deciding whether the process is running in a deployed
(production/preview/staging) environment versus local development.

Vercel always sets VERCEL=1 for every deployed function invocation, so deployed
environments are detected even if ENVIRONMENT is not explicitly configured. This
prevents development-only conveniences (OTP bypass, console email fallback,
unauthenticated role registration, synthetic data, public API docs) from being
silently enabled in production because an optional variable was left unset.
"""

import os
from typing import List


def is_deployed_environment() -> bool:
    """True for any deployed runtime (Vercel production/preview, or ENVIRONMENT=production/staging)."""
    if os.getenv("VERCEL"):
        return True
    return os.getenv("ENVIRONMENT", "development").strip().lower() in ("production", "staging")


def get_cors_allowed_origins() -> List[str]:
    """
    Explicit CORS allow-list.

    The CIP frontend is served by the same FastAPI app (same origin), so browsers do not
    need CORS for normal use. Cross-origin access is limited to explicitly configured
    origins (CORS_ALLOWED_ORIGINS, comma-separated) plus the current Vercel deployment URL.
    Local development additionally allows the local dev server origins.
    """
    origins: List[str] = [
        "https://cip-ruby.vercel.app",
    ]
    for item in os.getenv("CORS_ALLOWED_ORIGINS", "").split(","):
        cleaned = item.strip().rstrip("/")
        if cleaned and cleaned != "*" and cleaned not in origins:
            origins.append(cleaned)
    for var in ("VERCEL_PROJECT_PRODUCTION_URL", "VERCEL_URL"):
        host = os.getenv(var, "").strip().rstrip("/")
        if host:
            origin = host if host.startswith("http") else f"https://{host}"
            if origin not in origins:
                origins.append(origin)
    if not is_deployed_environment():
        for dev_origin in ("http://localhost:8000", "http://127.0.0.1:8000"):
            if dev_origin not in origins:
                origins.append(dev_origin)
    return origins
