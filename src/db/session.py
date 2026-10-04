"""
Database Session and Engine Management for AI CRISS.
Supports SQLite (aiosqlite) by default for zero-config local operation,
and PostgreSQL via asyncpg if configured via DATABASE_URL.
"""

import os
import logging
from typing import AsyncGenerator
from fastapi import HTTPException, status
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from src.db.models import Base
from src.runtime_env import is_deployed_environment

logger = logging.getLogger("ai_criss.db")

_is_deployed = is_deployed_environment()
_raw_db_url = os.getenv("DATABASE_URL", "").strip()

# Production Persistence Requirement (Section 5):
# Deployed environments (Vercel/production/staging) MUST NOT silently fall back to ephemeral /tmp SQLite.
# They require durable shared persistence (PostgreSQL / Supabase).
if _is_deployed and (not _raw_db_url or "sqlite" in _raw_db_url.lower()):
    DATABASE_URL = None
    _production_db_error = (
        "CRITICAL PERSISTENCE DEFECT: DATABASE_URL is not configured in deployed production environment. "
        "Ephemeral /tmp SQLite fallback is forbidden to prevent data loss. "
        "Please configure DATABASE_URL in Vercel project environment variables (Supabase/PostgreSQL)."
    )
else:
    _production_db_error = None
    if _raw_db_url:
        DATABASE_URL = _raw_db_url
    else:
        DATABASE_URL = "sqlite+aiosqlite:///./ai_criss.db"

    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
    elif DATABASE_URL.startswith("postgresql://") and not DATABASE_URL.startswith("postgresql+asyncpg://"):
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

from sqlalchemy.pool import NullPool

_is_serverless = bool(os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME"))

if DATABASE_URL is not None:
    _connect_args = {"timeout": 30} if "sqlite" in DATABASE_URL else {}
    _engine_kwargs = {
        "echo": False,
        "future": True,
        "connect_args": _connect_args,
    }
    if "sqlite" not in DATABASE_URL:
        _engine_kwargs["pool_pre_ping"] = True
        if _is_serverless:
            _engine_kwargs["poolclass"] = NullPool
        else:
            _engine_kwargs["pool_size"] = 10
            _engine_kwargs["max_overflow"] = 20
            _engine_kwargs["pool_recycle"] = 300

    engine = create_async_engine(
        DATABASE_URL,
        **_engine_kwargs
    )
    async_session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False
    )
else:
    engine = None
    async_session_factory = None


def _migrate_sqlite_columns(sync_conn) -> None:
    """Idempotently add new CIP / Google OAuth / Phase 1 columns to existing SQLite tables."""
    dialect = sync_conn.dialect.name
    if dialect != "sqlite":
        return

    inst_cols = {
        row[1] for row in sync_conn.execute(text("PRAGMA table_info(institutions)")).fetchall()
    }
    inst_migrations = [
        ("city", "VARCHAR(128)"),
        ("entity_category", "VARCHAR(64) DEFAULT 'educational_institution'"),
        ("entity_category_other", "VARCHAR(255)"),
        ("entity_type", "VARCHAR(64) DEFAULT 'institution'"),
        ("entity_type_other", "VARCHAR(255)"),
        ("ownership_governance", "VARCHAR(128)"),
        ("ownership_governance_other", "VARCHAR(255)"),
        ("education_level", "VARCHAR(128)"),
        ("education_entity_type", "VARCHAR(128)"),
        ("education_entity_type_other", "VARCHAR(255)"),
        ("university_type", "VARCHAR(128)"),
        ("university_type_other", "VARCHAR(255)"),
        ("academic_domain", "VARCHAR(255)"),
        ("academic_domains_json", "TEXT"),
        ("academic_domain_other", "VARCHAR(255)"),
        ("parent_organization_id", "VARCHAR(64)"),
        ("organization_id", "VARCHAR(64)"),
        ("owner_user_id", "VARCHAR(64)"),
        ("established_year", "INTEGER"),
        ("website", "VARCHAR(255)"),
        ("contact_email", "VARCHAR(255)"),
        ("regulatory_body", "VARCHAR(128)"),
        ("affiliation_details", "VARCHAR(255)"),
        ("latitude", "FLOAT"),
        ("longitude", "FLOAT"),
        ("active_dataset_id", "VARCHAR(64)"),
    ]
    for col_name, col_def in inst_migrations:
        if inst_cols and col_name not in inst_cols:
            sync_conn.execute(text(f"ALTER TABLE institutions ADD COLUMN {col_name} {col_def}"))

    user_cols = {
        row[1] for row in sync_conn.execute(text("PRAGMA table_info(users)")).fetchall()
    }
    user_migrations = [
        ("auth_provider", "VARCHAR(32) DEFAULT 'local'"),
        ("google_sub", "VARCHAR(128)"),
        ("full_name", "VARCHAR(255)"),
        ("job_title", "VARCHAR(128)"),
        ("phone", "VARCHAR(64)"),
        ("department_or_unit", "VARCHAR(128)"),
        ("organization_id", "VARCHAR(64)"),
        ("primary_institution_id", "VARCHAR(64)"),
        ("onboarding_completed", "BOOLEAN DEFAULT 0"),
        ("is_verified", "BOOLEAN DEFAULT 1"),
    ]
    for col_name, col_def in user_migrations:
        if user_cols and col_name not in user_cols:
            sync_conn.execute(text(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}"))

    sig_cols = {
        row[1] for row in sync_conn.execute(text("PRAGMA table_info(discovered_signals)")).fetchall()
    }
    sig_migrations = [
        ("fingerprint", "VARCHAR(64)"),
    ]
    for col_name, col_def in sig_migrations:
        if sig_cols and col_name not in sig_cols:
            sync_conn.execute(text(f"ALTER TABLE discovered_signals ADD COLUMN {col_name} {col_def}"))

    sync_conn.execute(text("CREATE INDEX IF NOT EXISTS idx_disc_sig_fp ON discovered_signals (institution_id, fingerprint)"))
    sync_conn.execute(text("CREATE INDEX IF NOT EXISTS idx_disc_sig_ident ON discovered_signals (institution_id, domain, metric_name, academic_year, department)"))
    sync_conn.execute(text("CREATE INDEX IF NOT EXISTS idx_otp_email_purpose ON otp_verifications (email, purpose, used_at)"))


if engine is not None and DATABASE_URL and "sqlite" in DATABASE_URL:
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


async def init_db() -> None:
    """Create all database tables if they do not exist and apply idempotent column migrations."""
    if engine is None:
        logger.error(_production_db_error)
        return
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_sqlite_columns)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency generator for FastAPI endpoints."""
    if engine is None or async_session_factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_production_db_error or "Persistent production database is unconfigured. DATABASE_URL is required."
        )
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
