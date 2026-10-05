"""Tests for JWT & RBAC Authentication Module (src/api/auth.py)."""

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from src.db.models import Base, UserModel
from src.db.repository import UserRepository
from src.api.auth import (
    get_password_hash,
    verify_password,
    create_access_token,
    get_current_user,
    require_role
)

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

@pytest_asyncio.fixture
async def auth_session():
    test_engine = create_async_engine(TEST_DB_URL, echo=False)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        # Create test users
        await UserRepository.create_user(
            session, "usr_admin", "admin@aicriss.org", get_password_hash("adminpass123"), role="SuperAdmin"
        )
        await UserRepository.create_user(
            session, "usr_auditor", "auditor@aicriss.org", get_password_hash("auditpass123"), role="Auditor"
        )
        await UserRepository.create_user(
            session, "usr_viewer", "viewer@aicriss.org", get_password_hash("viewpass123"), role="Viewer"
        )
        yield session

    await test_engine.dispose()

def test_password_hashing():
    pwd = "MySecretCrisisPassword2026!"
    hashed = get_password_hash(pwd)
    assert hashed != pwd
    assert verify_password(pwd, hashed) is True
    assert verify_password("WrongPassword", hashed) is False

def test_token_creation():
    token = create_access_token("usr_01", "test@aicriss.org", "Analyst")
    assert isinstance(token, str)
    assert len(token) > 20

@pytest.mark.anyio
async def test_get_current_user_valid(auth_session: AsyncSession):
    token = create_access_token("usr_auditor", "auditor@aicriss.org", "Auditor")
    user = await get_current_user(token=token, session=auth_session)
    assert user.email == "auditor@aicriss.org"
    assert user.role == "Auditor"

@pytest.mark.anyio
async def test_get_current_user_invalid_token(auth_session: AsyncSession):
    with pytest.raises(HTTPException) as exc:
        await get_current_user(token="invalid.garbage.token", session=auth_session)
    assert exc.value.status_code == 401

@pytest.mark.anyio
async def test_require_role_rbac_enforcement(auth_session: AsyncSession):
    viewer_user = await UserRepository.get_by_email(auth_session, "viewer@aicriss.org")
    admin_user = await UserRepository.get_by_email(auth_session, "admin@aicriss.org")
    auditor_user = await UserRepository.get_by_email(auth_session, "auditor@aicriss.org")

    checker = require_role("Auditor")

    # Auditor is allowed
    passed_auditor = await checker(auditor_user)
    assert passed_auditor.email == "auditor@aicriss.org"

    # SuperAdmin is always allowed
    passed_admin = await checker(admin_user)
    assert passed_admin.email == "admin@aicriss.org"

    # Viewer is denied with 403 Forbidden
    with pytest.raises(HTTPException) as exc:
        await checker(viewer_user)
    assert exc.value.status_code == 403
    assert "Access denied" in exc.value.detail


@pytest.mark.anyio
async def test_reload_session_restoration(auth_session: AsyncSession):
    """Verify that reload session restoration via token preserves user identity and onboarding status."""
    token = create_access_token(
        "usr_auditor",
        "auditor@aicriss.org",
        "Auditor",
        onboarding_completed=True,
        primary_institution_id="INST_AUDIT_01"
    )
    user = await get_current_user(token=token, session=auth_session)
    assert user.email == "auditor@aicriss.org"
    assert user.onboarding_completed is True


@pytest.mark.anyio
async def test_deployed_environment_key_resilience(monkeypatch, auth_session: AsyncSession):
    """Verify that under deployed environments (e.g. VERCEL=1) without custom JWT_SECRET_KEY, decoding does not crash."""
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    from src.api.auth import SECRET_KEY
    assert SECRET_KEY is not None
    assert len(SECRET_KEY) >= 16
    token = create_access_token("usr_admin", "admin@aicriss.org", "SuperAdmin")
    user = await get_current_user(token=token, session=auth_session)
    assert user.email == "admin@aicriss.org"
