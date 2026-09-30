"""
Operational Readiness Review (ORR) Live Workflow Test Suite.
Executes realistic user workflows: deployment health check, RBAC access governance,
real institutional Excel ingestion (RYMEC), crisis scoring, policy simulation,
evidence dossier assembly, executive reporting, error recovery, and disaster backup/restore.
"""

import os
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.db.models import Base
from src.db.session import get_db_session
from src.db.backup import create_backup, restore_backup

REAL_EXCEL_PATH = "C:/Users/Dell/OneDrive/Documents/antigravity/project data set.xlsx"


@pytest_asyncio.fixture
async def operational_client():
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
async def test_operational_startup_and_health(operational_client: AsyncClient):
    """Workflow 1: Startup verification and live health monitoring."""
    resp = await operational_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "HEALTHY"
    assert data["database"] == "CONNECTED"
    assert data["layer_boundaries_verified"] is True
    # Verify response latency header injected by observability middleware
    assert "x-response-time-ms" in resp.headers


@pytest.mark.anyio
async def test_operational_root_and_dashboard(operational_client: AsyncClient):
    """Workflow 2: Dashboard access."""
    root_resp = await operational_client.get("/")
    assert root_resp.status_code == 200
    assert root_resp.json()["dashboard_url"] == "/dashboard/"

    dash_resp = await operational_client.get("/dashboard/", follow_redirects=True)
    assert dash_resp.status_code == 200
    assert "CIP" in dash_resp.text
    assert "Crisis Intelligence Platform" in dash_resp.text
    assert "Institutional Risk Score" in dash_resp.text
    assert "What-If Analysis" in dash_resp.text



@pytest.mark.anyio
async def test_operational_user_lifecycle_and_rbac(operational_client: AsyncClient):
    """Workflow 3: Multi-role user provisioning and RBAC enforcement."""
    # 1. Provision Auditor
    await operational_client.post(
        "/api/v1/auth/register",
        json={
            "user_id": "auditor_01",
            "email": "auditor@state.gov",
            "password": "AuditSecurePassword1!",
            "role": "Auditor"
        }
    )

    # 2. Provision Viewer
    await operational_client.post(
        "/api/v1/auth/register",
        json={
            "user_id": "viewer_01",
            "email": "dean@college.edu",
            "password": "DeanPassword2026!",
            "role": "Viewer"
        }
    )

    # 3. Authenticate Auditor
    audit_login = await operational_client.post(
        "/api/v1/auth/login",
        data={"username": "auditor@state.gov", "password": "AuditSecurePassword1!"}
    )
    assert audit_login.status_code == 200
    audit_token = audit_login.json()["access_token"]

    # 4. Authenticate Viewer
    view_login = await operational_client.post(
        "/api/v1/auth/login",
        data={"username": "dean@college.edu", "password": "DeanPassword2026!"}
    )
    assert view_login.status_code == 200
    view_token = view_login.json()["access_token"]

    # 5. RBAC Violation: Viewer attempts to trigger synthetic ingest (Restricted to Analyst/Auditor/SuperAdmin)
    denied_resp = await operational_client.post(
        "/api/v1/ingest/synthetic",
        headers={"Authorization": f"Bearer {view_token}"},
        json={"institution_id": "INST_HACK", "scenario": "HEALTHY"}
    )
    assert denied_resp.status_code == 403
    assert "Access denied" in denied_resp.json()["detail"]

    # 6. Authorized Action: Auditor triggers synthetic ingest
    allowed_resp = await operational_client.post(
        "/api/v1/ingest/synthetic",
        headers={"Authorization": f"Bearer {audit_token}"},
        json={"institution_id": "INST_AUDITED", "scenario": "HEALTHY", "start_year": 2020, "num_years": 4}
    )
    assert allowed_resp.status_code == 200
    assert allowed_resp.json()["status"] == "SUCCESS"


@pytest.mark.anyio
async def test_operational_real_dataset_workflow(operational_client: AsyncClient):
    """Workflow 4: Complete real-world institutional assessment cycle (RYMEC)."""
    if not os.path.exists(REAL_EXCEL_PATH):
        pytest.skip("Real Excel dataset not found on disk")

    # 1. Register SuperAdmin
    await operational_client.post(
        "/api/v1/auth/register",
        json={
            "user_id": "superadmin_01",
            "email": "chief@aicriss.edu",
            "password": "RootAdminPassword2026!",
            "role": "SuperAdmin"
        }
    )
    login_resp = await operational_client.post(
        "/api/v1/auth/login",
        data={"username": "chief@aicriss.edu", "password": "RootAdminPassword2026!"}
    )
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Ingest Real Excel Workbook
    with open(REAL_EXCEL_PATH, "rb") as f:
        file_bytes = f.read()

    files = {"file": ("project_data_set.xlsx", file_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    ingest_resp = await operational_client.post("/api/v1/ingest/excel", headers=headers, files=files)
    assert ingest_resp.status_code == 200
    ingest_res = ingest_resp.json()
    assert ingest_res["status"] == "SUCCESS"
    assert ingest_res["institution_id"] == "RYMEC"
    assert ingest_res["total_signals_ingested"] >= 90

    # 3. Evaluate Real Institutional Crisis Risk
    eval_resp = await operational_client.get("/api/v1/institutions/RYMEC/evaluate", headers=headers)
    assert eval_resp.status_code == 200
    assessment = eval_resp.json()
    assert assessment["institution_id"] == "RYMEC"
    assert assessment["risk_level"] in ["HIGH", "CRITICAL"]
    assert assessment["composite_risk_index"] >= 0.50
    assert "Placement" in assessment["primary_driving_signal"]
    assert len(assessment["recommended_mitigations"]) > 0

    # 4. Retrieve Evidence Dossier
    dossier_resp = await operational_client.get("/api/v1/institutions/RYMEC/dossier", headers=headers)
    assert dossier_resp.status_code == 200
    dossier = dossier_resp.json()
    assert dossier["institution_id"] == "RYMEC"
    assert dossier["signal_coverage"]["placements"] is True
    assert len(dossier["evidence_tokens"]) > 0

    # 5. Run Forward Trajectory Simulation
    sim_resp = await operational_client.post(
        "/api/v1/institutions/RYMEC/simulate",
        headers=headers,
        json={"years_forward": 3, "intervention_effects": {"placement_boost": 10.0, "vacancy_rate_reduction": 0.12}}
    )
    assert sim_resp.status_code == 200
    sim_data = sim_resp.json()
    assert sim_data["risk_reduction_achieved"] > 0.05
    assert sim_data["intervention_trajectory"][-1]["projected_cri"] < sim_data["status_quo_trajectory"][-1]["projected_cri"]

    # 6. Generate Grounded Executive Report
    rep_resp = await operational_client.get("/api/v1/institutions/RYMEC/report", headers=headers)
    assert rep_resp.status_code == 200
    report = rep_resp.json()
    assert report["institution_id"] == "RYMEC"
    assert "RYMEC" in report["executive_summary"]
    assert len(report["root_causes"]) > 0
    assert len(report["prioritized_actions"]) > 0


@pytest.mark.anyio
async def test_operational_error_handling_and_recovery(operational_client: AsyncClient):
    """Workflow 5: Robust error handling and graceful recovery."""
    # 1. Register user
    await operational_client.post(
        "/api/v1/auth/register",
        json={"user_id": "u_test", "email": "err@aicriss.edu", "password": "Password123!", "role": "Auditor"}
    )
    login_resp = await operational_client.post(
        "/api/v1/auth/login",
        data={"username": "err@aicriss.edu", "password": "Password123!"}
    )
    headers = {"Authorization": f"Bearer {login_resp.json()['access_token']}"}

    # 2. Reject non-excel upload
    bad_files = {"file": ("malicious.txt", b"plain text", "text/plain")}
    bad_resp = await operational_client.post("/api/v1/ingest/excel", headers=headers, files=bad_files)
    assert bad_resp.status_code == 400
    assert "Only .xlsx or .xlsm" in bad_resp.json()["detail"]

    # 3. Query non-existent institution
    not_found_resp = await operational_client.get("/api/v1/institutions/NON_EXISTENT_XYZ/evaluate", headers=headers)
    assert not_found_resp.status_code == 404
    assert "No signal records found" in not_found_resp.json()["detail"]


def test_operational_backup_and_recovery():
    """Workflow 6: Automated backup creation, integrity check, and restoration."""
    # Create temporary sqlite file
    test_db = "temp_test_operational.db"
    import sqlite3
    conn = sqlite3.connect(test_db)
    conn.execute("CREATE TABLE test_table (id INT, name TEXT);")
    conn.execute("INSERT INTO test_table VALUES (1, 'IntegrityTest');")
    conn.commit()
    conn.close()

    try:
        backup_result = create_backup(db_path=test_db, backup_dir="temp_backups")
        assert backup_result["status"] == "SUCCESS"
        assert backup_result["integrity"] == "VERIFIED_OK"
        backup_path = backup_result["backup_file"]
        assert os.path.exists(backup_path)

        # Corrupt the original and restore
        os.remove(test_db)
        restore_result = restore_backup(backup_filepath=backup_path, target_db_path=test_db)
        assert restore_result["status"] == "SUCCESS"
        assert restore_result["integrity"] == "VERIFIED_OK"

        # Verify restored contents
        verify_conn = sqlite3.connect(test_db)
        cur = verify_conn.cursor()
        cur.execute("SELECT name FROM test_table WHERE id = 1;")
        row = cur.fetchone()
        verify_conn.close()
        assert row[0] == "IntegrityTest"
    finally:
        if os.path.exists(test_db):
            os.remove(test_db)
        if os.path.exists("temp_backups"):
            import shutil
            shutil.rmtree("temp_backups")
