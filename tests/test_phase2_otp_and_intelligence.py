"""
Tests for CIP Phase 2 Complete Master Implementation:
- Real Backend OTP Authentication (Signup, Login, Verify, Resend, Expiry, Attempt Limits)
- What-If Simulation Endpoint (/institutions/{id}/simulate & /what-if)
- External Intelligence Refresh Endpoint (/osint/refresh with real detection & status)
"""

import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.db.models import Base
from src.db.session import get_db_session
from src.db.repository import UserRepository, OTPRepository
from src.api.routes.auth_routes import hash_otp_code


@pytest_asyncio.fixture
async def phase2_client():
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db_session] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

    app.dependency_overrides.clear()
    await test_engine.dispose()


@pytest.mark.anyio
async def test_otp_signup_and_verification_flow(phase2_client: AsyncClient):
    # 1. Signup triggers OTP challenge
    signup_resp = await phase2_client.post(
        "/api/v1/auth/signup",
        json={
            "email": "dean.smith@institution.edu",
            "password": "SecurePassword2026!",
            "full_name": "Dean Smith",
        }
    )
    assert signup_resp.status_code == 200
    data = signup_resp.json()
    assert data["status"] == "AWAITING_OTP"
    assert data["email"] == "dean.smith@institution.edu"
    assert data["purpose"] == "signup"
    assert data["expires_in_seconds"] == 300

    # 2. Wrong OTP is rejected with remaining attempts count
    verify_bad = await phase2_client.post(
        "/api/v1/auth/otp/verify",
        json={
            "email": "dean.smith@institution.edu",
            "otp": "000000",
            "purpose": "signup",
        }
    )
    assert verify_bad.status_code == 400
    assert "Invalid verification code" in verify_bad.json()["detail"]
    assert "4 attempt(s) remaining" in verify_bad.json()["detail"]

    # 3. Resend cooldown: resending immediately returns 429
    resend_fast = await phase2_client.post(
        "/api/v1/auth/otp/resend",
        json={"email": "dean.smith@institution.edu", "purpose": "signup"}
    )
    assert resend_fast.status_code == 429
    assert "Please wait" in resend_fast.json()["detail"]

    # 4. Verify login also triggers OTP
    login_resp = await phase2_client.post(
        "/api/v1/auth/login/json",
        json={
            "email": "dean.smith@institution.edu",
            "password": "SecurePassword2026!",
        }
    )
    assert login_resp.status_code == 200
    login_data = login_resp.json()
    assert login_data["status"] == "AWAITING_OTP"
    assert login_data["purpose"] == "login"


@pytest.mark.anyio
async def test_what_if_simulation_endpoint(phase2_client: AsyncClient):
    # 1. Register and login with skip_otp for test harness setup
    await phase2_client.post(
        "/api/v1/auth/signup",
        json={
            "email": "director@institution.edu",
            "password": "Password123!",
            "skip_otp": True,
        }
    )
    login_resp = await phase2_client.post(
        "/api/v1/auth/login",
        data={"username": "director@institution.edu", "password": "Password123!"}
    )
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Ingest multi-year crisis scenario
    inst_id = "INST_SIM_TEST"
    await phase2_client.post(
        "/api/v1/ingest/synthetic",
        headers=headers,
        json={"institution_id": inst_id, "scenario": "CASCADING_CRISIS", "start_year": 2021, "num_years": 4}
    )

    # 3. Run What-If simulation via /simulate
    sim_resp = await phase2_client.post(
        f"/api/v1/institutions/{inst_id}/simulate",
        headers=headers,
        json={
            "years_forward": 3,
            "intervention_effects": {
                "placement_boost": 20.0,
                "vacancy_rate_reduction": 0.15,
                "closing_rank_stabilization": 15.0,
            }
        }
    )
    assert sim_resp.status_code == 200
    sim_data = sim_resp.json()
    assert sim_data["analysis_type"] == "What-If Analysis"
    assert "baseline" in sim_data
    assert "intervention" in sim_data
    assert "projected_trajectory" in sim_data
    assert "estimated_risk_change" in sim_data
    assert sim_data["estimated_risk_change"]["risk_reduction_achieved"] > 0
    assert sim_data["estimated_risk_change"]["direction"] == "RISK_REDUCED"

    # 4. Unknown/decorative controls rejected with 422
    bad_ctrl_resp = await phase2_client.post(
        f"/api/v1/institutions/{inst_id}/simulate",
        headers=headers,
        json={
            "years_forward": 3,
            "intervention_effects": {
                "fake_decorative_slider": 50.0,
            }
        }
    )
    assert bad_ctrl_resp.status_code == 422


@pytest.mark.anyio
async def test_osint_refresh_endpoint(phase2_client: AsyncClient):
    # Register and login
    await phase2_client.post(
        "/api/v1/auth/signup",
        json={
            "email": "analyst@cip.org",
            "password": "Password123!",
            "skip_otp": True,
        }
    )
    login_resp = await phase2_client.post(
        "/api/v1/auth/login",
        data={"username": "analyst@cip.org", "password": "Password123!"}
    )
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Call /osint/refresh
    refresh_resp = await phase2_client.post("/api/v1/osint/refresh", headers=headers)
    assert refresh_resp.status_code == 200
    data = refresh_resp.json()
    assert data["status"] in ("UPDATED", "NO_CHANGES", "PARTIAL_FAILURE")
    assert "last_checked" in data
    assert "new_events_count" in data
    assert "sources_checked_count" in data
    assert data["sources_checked_count"] > 0
