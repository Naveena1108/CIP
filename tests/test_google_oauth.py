"""
Integration and Security Tests for Google OAuth 2.0, Entity Onboarding,
User/Organization Isolation, and Session Logout.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.db.models import Base
from src.db.session import get_db_session
from src.api.auth import create_google_oauth_state
import src.api.routes.auth_routes as auth_routes_mod


@pytest_asyncio.fixture
async def oauth_client():
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
    async with AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
        yield client

    app.dependency_overrides.clear()
    await test_engine.dispose()


@pytest.mark.anyio
async def test_google_oauth_config_discovery(oauth_client: AsyncClient, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "super-secret-never-expose")

    resp = await oauth_client.get("/api/v1/auth/google/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is True
    assert data["client_id"] == "test-client-id.apps.googleusercontent.com"
    assert "super-secret-never-expose" not in resp.text
    assert data["detected_origin"] == "http://localhost:8000"
    assert data["callback_path"] == "/api/v1/auth/google/callback"
    assert data["redirect_uri"] == "http://localhost:8000/api/v1/auth/google/callback"
    assert "http://localhost:8000" in data["authorized_javascript_origins"]
    assert "http://127.0.0.1:8000" in data["authorized_javascript_origins"]
    assert "http://localhost:8000/api/v1/auth/google/callback" in data["authorized_redirect_uris"]
    assert "http://127.0.0.1:8000/api/v1/auth/google/callback" in data["authorized_redirect_uris"]


@pytest.mark.anyio
async def test_google_oauth_error_handling_matrix(oauth_client: AsyncClient, monkeypatch):
    # 1. Missing configuration
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    resp_missing = await oauth_client.get("/api/v1/auth/google/login?format=json")
    assert resp_missing.status_code == 503
    assert resp_missing.json()["error_code"] == "missing_configuration"

    # Configure mock client credentials for remaining error tests
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")

    # 2. Origin mismatch
    resp_origin = await oauth_client.get(
        "/api/v1/auth/google/login?format=json&origin=http://untrusted.example.org"
    )
    assert resp_origin.status_code == 400
    assert resp_origin.json()["error_code"] == "origin_mismatch"

    # 3. Redirect URI mismatch
    resp_redir = await oauth_client.get(
        "/api/v1/auth/google/login?format=json&redirect_uri=http://localhost:8000/wrong/callback"
    )
    assert resp_redir.status_code == 400
    assert resp_redir.json()["error_code"] == "redirect_uri_mismatch"

    # 4. Cancelled login
    resp_cancel = await oauth_client.get(
        "/api/v1/auth/google/callback?format=json&error=access_denied"
    )
    assert resp_cancel.status_code == 400
    assert resp_cancel.json()["error_code"] == "cancelled_login"

    # 5. Invalid/expired credential (tampered state)
    resp_invalid_state = await oauth_client.get(
        "/api/v1/auth/google/callback?format=json&code=valid_code&state=invalid.jwt.token"
    )
    assert resp_invalid_state.status_code == 401
    assert resp_invalid_state.json()["error_code"] == "invalid_or_expired_credential"

    # 6. Provider failure
    valid_state = create_google_oauth_state(
        "http://localhost:8000", "http://localhost:8000/api/v1/auth/google/callback"
    )

    async def mock_provider_down(*args, **kwargs):
        from fastapi import HTTPException
        raise HTTPException(status_code=502, detail="provider_failure: Simulated upstream 503.")

    monkeypatch.setattr(auth_routes_mod, "_exchange_google_code_for_profile", mock_provider_down)
    resp_prov = await oauth_client.get(
        f"/api/v1/auth/google/callback?format=json&code=some_code&state={valid_state}"
    )
    assert resp_prov.status_code == 502
    assert resp_prov.json()["error_code"] == "provider_failure"


@pytest.mark.anyio
async def test_google_login_onboarding_deduplication_isolation_and_logout(
    oauth_client: AsyncClient, monkeypatch
):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")

    async def mock_google_profile_new(code: str, client_id: str, client_secret: str, redirect_uri: str):
        return {
            "sub": "google-sub-990011",
            "email": "registrar@coastaluniv.edu",
            "email_verified": True,
            "name": "Dr. Kavitha Rao",
        }

    monkeypatch.setattr(auth_routes_mod, "_exchange_google_code_for_profile", mock_google_profile_new)

    # Initiate login to get application-generated state & redirect_uri
    login_init = await oauth_client.get("/api/v1/auth/google/login?format=json")
    assert login_init.status_code == 200
    init_data = login_init.json()
    assert init_data["redirect_uri"] == "http://localhost:8000/api/v1/auth/google/callback"
    state_token = init_data["state"]

    # Complete callback for new Google user
    cb_resp = await oauth_client.get(
        f"/api/v1/auth/google/callback?format=json&code=auth_code_123&state={state_token}"
    )
    assert cb_resp.status_code == 200
    token_data = cb_resp.json()
    assert token_data["email"] == "registrar@coastaluniv.edu"
    assert token_data["auth_provider"] == "google"
    assert token_data["onboarding_required"] is True
    jwt_user1 = token_data["access_token"]
    headers_user1 = {"Authorization": f"Bearer {jwt_user1}"}

    # Verify /me works and reports onboarding_required=True
    me_resp = await oauth_client.get("/api/v1/auth/me", headers=headers_user1)
    assert me_resp.status_code == 200
    assert me_resp.json()["onboarding_required"] is True

    # Verify "other" entity classification requires custom text
    bad_onboard = await oauth_client.post(
        "/api/v1/auth/onboarding",
        headers=headers_user1,
        json={
            "entity_id": "ENT_COASTAL_01",
            "entity_name": "Coastal Statutory Council",
            "entity_type": "other",
            "entity_type_other": "   ",
            "ownership_governance": "Autonomous Public",
            "education_level": "Postgraduate",
            "academic_domain": "Marine Sciences",
        },
    )
    assert bad_onboard.status_code == 422

    # Complete valid entity onboarding with "other" custom classification
    good_onboard = await oauth_client.post(
        "/api/v1/auth/onboarding",
        headers=headers_user1,
        json={
            "entity_id": "ENT_COASTAL_01",
            "entity_name": "Coastal Statutory Council",
            "entity_type": "other",
            "entity_type_other": "Inter-State Autonomous Maritime Research Council",
            "ownership_governance": "Autonomous Public",
            "education_level": "Postgraduate & Doctoral",
            "academic_domain": "Marine Sciences",
        },
    )
    assert good_onboard.status_code == 200
    onboard_json = good_onboard.json()
    assert onboard_json["status"] == "ONBOARDING_COMPLETED"
    assert onboard_json["user"]["onboarding_completed"] is True
    assert onboard_json["user"]["onboarding_required"] is False
    assert onboard_json["entity"]["entity_type"] == "other"
    assert onboard_json["entity"]["entity_type_other"] == "Inter-State Autonomous Maritime Research Council"

    # Re-authenticate the same Google user -> must NOT duplicate user and must have onboarding_required=False
    cb_repeat = await oauth_client.get(
        f"/api/v1/auth/google/callback?format=json&code=auth_code_456&state={state_token}"
    )
    assert cb_repeat.status_code == 200
    repeat_data = cb_repeat.json()
    assert repeat_data["user_id"] == token_data["user_id"]
    assert repeat_data["onboarding_required"] is False
    assert repeat_data["primary_institution_id"] == "ENT_COASTAL_01"

    # Conflicting duplicate account check: different Google sub trying to claim same email
    async def mock_google_conflict(code: str, client_id: str, client_secret: str, redirect_uri: str):
        return {
            "sub": "different-google-sub-777",
            "email": "registrar@coastaluniv.edu",
            "email_verified": True,
            "name": "Impostor Account",
        }

    monkeypatch.setattr(auth_routes_mod, "_exchange_google_code_for_profile", mock_google_conflict)
    cb_conflict = await oauth_client.get(
        f"/api/v1/auth/google/callback?format=json&code=auth_code_789&state={state_token}"
    )
    assert cb_conflict.status_code == 409
    assert cb_conflict.json()["error_code"] == "duplicate_account_conflict"

    # Verify User & Organization Isolation: a second user cannot ingest/evaluate User 1's scoped entity
    async def mock_google_user2(code: str, client_id: str, client_secret: str, redirect_uri: str):
        return {
            "sub": "google-sub-222222",
            "email": "analyst@otherorg.org",
            "email_verified": True,
            "name": "Second User",
        }

    monkeypatch.setattr(auth_routes_mod, "_exchange_google_code_for_profile", mock_google_user2)
    cb_user2 = await oauth_client.get(
        f"/api/v1/auth/google/callback?format=json&code=auth_code_u2&state={state_token}"
    )
    assert cb_user2.status_code == 200
    headers_user2 = {"Authorization": f"Bearer {cb_user2.json()['access_token']}"}

    # User 2 attempts to ingest synthetic data into User 1's private entity ENT_COASTAL_01 -> 403 Forbidden
    forbidden_ingest = await oauth_client.post(
        "/api/v1/ingest/synthetic",
        headers=headers_user2,
        json={
            "institution_id": "ENT_COASTAL_01",
            "scenario": "HEALTHY",
            "start_year": 2020,
            "num_years": 5,
        },
    )
    assert forbidden_ingest.status_code == 403

    # User 1 can ingest and evaluate their own entity ENT_COASTAL_01 -> 200 OK
    allowed_ingest = await oauth_client.post(
        "/api/v1/ingest/synthetic",
        headers=headers_user1,
        json={
            "institution_id": "ENT_COASTAL_01",
            "scenario": "HEALTHY",
            "start_year": 2020,
            "num_years": 5,
        },
    )
    assert allowed_ingest.status_code == 200

    # User 2 still cannot evaluate User 1's entity -> 403 Forbidden
    forbidden_eval = await oauth_client.get(
        "/api/v1/institutions/ENT_COASTAL_01/evaluate",
        headers=headers_user2,
    )
    assert forbidden_eval.status_code == 403

    # Verify Logout revokes User 1's session token
    logout_resp = await oauth_client.post("/api/v1/auth/logout", headers=headers_user1)
    assert logout_resp.status_code == 200
    assert logout_resp.json()["status"] == "LOGGED_OUT"

    # Subsequent protected call with logged-out token must return 401
    after_logout = await oauth_client.get("/api/v1/auth/me", headers=headers_user1)
    assert after_logout.status_code == 401
