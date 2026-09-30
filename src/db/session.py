"""
Database Session and Engine Management for AI CRISS.
Supports SQLite (aiosqlite) by default for zero-config local operation,
and PostgreSQL via asyncpg if configured via DATABASE_URL.
"""

import os
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from src.db.models import Base

_default_sqlite_url = (
    "sqlite+aiosqlite:////tmp/ai_criss.db"
    if os.getenv("VERCEL")
    else "sqlite+aiosqlite:///./ai_criss.db"
)
DATABASE_URL = os.getenv("DATABASE_URL", _default_sqlite_url)

_connect_args = {"timeout": 30} if "sqlite" in DATABASE_URL else {}

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    connect_args=_connect_args,
)

from sqlalchemy import event, text


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:
    if "sqlite" in DATABASE_URL:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)


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
    ]
    for col_name, col_def in user_migrations:
        if user_cols and col_name not in user_cols:
            sync_conn.execute(text(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}"))


async def init_db() -> None:
    """Create all database tables if they do not exist and apply idempotent column migrations."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_sqlite_columns)



async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency generator for FastAPI endpoints."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
