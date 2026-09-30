"""Integration tests for FastAPI REST API (src/api/)."""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.db.models import Base
from src.db.session import get_db_session


@pytest_asyncio.fixture
async def api_client():
    # Use StaticPool so all async sessions share the exact same in-memory SQLite database
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False
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
async def test_health_endpoint(api_client: AsyncClient):
    resp = await api_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "HEALTHY"
    assert data["layer_boundaries_verified"] is True


@pytest.mark.anyio
async def test_dashboard_cockpit_endpoint(api_client: AsyncClient):
    resp = await api_client.get("/dashboard/", follow_redirects=True)
    assert resp.status_code == 200
    assert "CIP" in resp.text
    assert "Crisis Intelligence Platform" in resp.text


@pytest.mark.anyio
async def test_unauthenticated_access_rejected(api_client: AsyncClient):
    resp = await api_client.get("/api/v1/institutions")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_full_api_workflow(api_client: AsyncClient):
    # 1. Register a SuperAdmin user
    reg_resp = await api_client.post(
        "/api/v1/auth/register",
        json={
            "user_id": "admin_01",
            "email": "admin@aicriss.edu",
            "password": "SuperSecretPassword123!",
            "role": "SuperAdmin"
        }
    )
    assert reg_resp.status_code == 200

    # 2. Login to receive JWT token
    login_resp = await api_client.post(
        "/api/v1/auth/login",
        data={"username": "admin@aicriss.edu", "password": "SuperSecretPassword123!"}
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 3. Check /me
    me_resp = await api_client.get("/api/v1/auth/me", headers=headers)
    assert me_resp.status_code == 200
    assert me_resp.json()["email"] == "admin@aicriss.edu"

    # 4. Ingest Synthetic Crisis Scenario
    ingest_resp = await api_client.post(
        "/api/v1/ingest/synthetic",
        headers=headers,
        json={
            "institution_id": "INST_API_TEST",
            "scenario": "CASCADING_CRISIS",
            "start_year": 2020,
            "num_years": 5
        }
    )
    assert ingest_resp.status_code == 200
    ingest_data = ingest_resp.json()
    assert ingest_data["status"] == "SUCCESS"
    assert ingest_data["total_signals_ingested"] == 15

    # 5. List Institutions
    inst_list_resp = await api_client.get("/api/v1/institutions", headers=headers)
    assert inst_list_resp.status_code == 200
    assert len(inst_list_resp.json()) == 1
    assert inst_list_resp.json()[0]["id"] == "INST_API_TEST"

    # 6. Evaluate Institution
    eval_resp = await api_client.get("/api/v1/institutions/INST_API_TEST/evaluate", headers=headers)
    assert eval_resp.status_code == 200
    eval_data = eval_resp.json()
    assert eval_data["institution_id"] == "INST_API_TEST"
    assert eval_data["risk_level"] in ["CRITICAL", "HIGH"]
    assert eval_data["composite_risk_index"] >= 0.50
    assert len(eval_data["anomalies_detected"]) > 0

    # 7. Get Evidence Dossier
    dossier_resp = await api_client.get("/api/v1/institutions/INST_API_TEST/dossier", headers=headers)
    assert dossier_resp.status_code == 200
    dossier = dossier_resp.json()
    assert dossier["institution_id"] == "INST_API_TEST"
    assert dossier["total_anomalies"] > 0
    assert len(dossier["evidence_tokens"]) > 0

    # 8. Simulate Forward Trajectory
    sim_resp = await api_client.post(
        "/api/v1/institutions/INST_API_TEST/simulate",
        headers=headers,
        json={"years_forward": 3, "intervention_effects": {"placement_boost": 8.0, "vacancy_rate_reduction": 0.10}}
    )
    assert sim_resp.status_code == 200
    sim_data = sim_resp.json()
    assert len(sim_data["status_quo_trajectory"]) == 3
    assert len(sim_data["intervention_trajectory"]) == 3
    assert sim_data["risk_reduction_achieved"] >= 0

    # 9. Get Executive Report
    report_resp = await api_client.get("/api/v1/institutions/INST_API_TEST/report", headers=headers)
    assert report_resp.status_code == 200
    report_data = report_resp.json()
    assert report_data["institution_id"] == "INST_API_TEST"
    assert len(report_data["root_causes"]) > 0
    assert len(report_data["investigation_priorities"]) > 0


@pytest.mark.anyio
async def test_rbac_and_invalid_role_validation(api_client: AsyncClient):
    # 1. Invalid role should be rejected with 422
    bad_role_resp = await api_client.post(
        "/api/v1/auth/register",
        json={
            "user_id": "bad_role_01",
            "email": "badrole@aicriss.edu",
            "password": "ValidPassword123!",
            "role": "RootHacker"
        }
    )
    assert bad_role_resp.status_code == 422

    # 2. Register a Viewer user and verify 403 on write/ingest endpoints
    viewer_reg = await api_client.post(
        "/api/v1/auth/register",
        json={
            "user_id": "viewer_01",
            "email": "viewer@aicriss.edu",
            "password": "ViewerPassword123!",
            "role": "Viewer"
        }
    )
    assert viewer_reg.status_code == 200

    login_resp = await api_client.post(
        "/api/v1/auth/login",
        data={"username": "viewer@aicriss.edu", "password": "ViewerPassword123!"}
    )
    assert login_resp.status_code == 200
    viewer_headers = {"Authorization": f"Bearer {login_resp.json()['access_token']}"}

    forbidden_resp = await api_client.post(
        "/api/v1/ingest/synthetic",
        headers=viewer_headers,
        json={"institution_id": "INST_FORBIDDEN", "scenario": "HEALTHY"}
    )
    assert forbidden_resp.status_code == 403

