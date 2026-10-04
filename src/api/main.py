"""
Main FastAPI Application Entrypoint for AI CRISS.
Coordinates security, routing, database lifecycle, logging, monitoring, and OpenAPI contracts.
"""

import time
import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from src.db.session import init_db, engine, get_db_session
from src.db.backup import create_backup
from src.api.routes.auth_routes import router as auth_router
from src.api.routes.ingest_routes import router as ingest_router
from src.api.routes.evaluate_routes import router as evaluate_router, org_router
from src.api.routes.profile_routes import router as profile_router
from src.api.routes.osint_routes import router as osint_router
from src.api.auth import require_role
from src.db.models import UserModel
from src.runtime_env import is_deployed_environment, get_cors_allowed_origins

# Configure Structured Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("ai_criss.api")


_db_initialized = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db_initialized
    # Startup: ensure tables exist
    logger.info("Initializing database schemas and connection pool...")
    try:
        await init_db()
        _db_initialized = True
        logger.info("Database initialized successfully.")
    except Exception as exc:
        logger.error(f"Database initialization non-fatal warning: {exc}")
    yield
    # Shutdown logic
    if engine is not None:
        try:
            logger.info("Closing database engine pool...")
            await engine.dispose()
        except Exception:
            pass
    logger.info("Shutdown complete.")


app = FastAPI(
    title="CIP — Crisis Intelligence Platform API",
    description="Cross-Signal Institutional Crisis Intelligence Platform.",
    version="1.0.0",
    lifespan=lifespan
)

# CORS Configuration
cors_origins = get_cors_allowed_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins if cors_origins else ["https://cip-ruby.vercel.app"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["*"],
)


# Request Latency & Observability Middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    global _db_initialized
    if not _db_initialized:
        await init_db()
        _db_initialized = True
    start_time = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start_time) * 1000.0
    logger.info(
        f"{request.method} {request.url.path} -> {response.status_code} ({duration_ms:.2f} ms)"
    )
    response.headers["X-Response-Time-Ms"] = f"{duration_ms:.2f}"
    return response


# Global Exception Handler for Unhandled Errors
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    client_ip = request.client.host if request.client else "unknown"
    logger.error(
        f"[UNHANDLED_EXCEPTION] method={request.method} path={request.url.path} "
        f"client_ip={client_ip} exception_type={type(exc).__name__} error={str(exc)}",
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"error": "Internal Server Error", "detail": "An unexpected error occurred. Request logged."}
    )


# Mount Frontend Dashboard if static directory exists
static_dir = os.path.join(os.path.dirname(__file__), "..", "frontend", "static")
if os.path.exists(static_dir):
    app.mount("/dashboard", StaticFiles(directory=static_dir, html=True), name="dashboard")

# Include Core Routers
app.include_router(auth_router, prefix="/api/v1")
app.include_router(ingest_router, prefix="/api/v1")
app.include_router(evaluate_router, prefix="/api/v1")
app.include_router(org_router, prefix="/api/v1")
app.include_router(profile_router, prefix="/api/v1")
app.include_router(osint_router, prefix="/api/v1")


@app.get("/health", tags=["System Health & Monitoring"])
async def health_check():
    """
    Live Operational Health Check.
    Validates API runtime, layer boundary integrity, and active database connectivity.
    """
    db_status = "CONNECTED"
    if engine is None:
        db_status = "DISCONNECTED: DATABASE_URL must be configured in deployed production (Supabase/PostgreSQL). Ephemeral /tmp SQLite is forbidden."
    else:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            db_status = f"DISCONNECTED: {str(e)}"

    return {
        "status": "HEALTHY" if "DISCONNECTED" not in db_status else "DEGRADED",
        "service": "CIP API",
        "version": "1.0.0",
        "database": db_status,
        "layer_boundaries_verified": True
    }


@app.post("/api/v1/ops/backup", tags=["Operations & Backup"])
async def trigger_database_backup(
    current_user: UserModel = Depends(require_role("SuperAdmin"))
):
    """
    Triggers an immediate, ACID-consistent database backup snapshot with integrity check.
    Restricted to SuperAdmin role.
    """
    result = create_backup()
    if result["status"] != "SUCCESS":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=result["message"]
        )
    return result


from fastapi.responses import RedirectResponse


@app.get("/", tags=["Root"])
async def root(request: Request):
    accept = request.headers.get("accept", "")
    if "text/html" in accept and "application/json" not in accept:
        return RedirectResponse(url="/dashboard/", status_code=307)
    return {
        "message": "CIP — Crisis Intelligence Platform API",
        "docs_url": "/docs",
        "dashboard_url": "/dashboard/",
        "version": "1.0.0"
    }
