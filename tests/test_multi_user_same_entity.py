"""
Integration test verifying multi-user onboarding for the same entity and persistent database storage.
Ensures that multiple users from the same institution can register, join the same entity without 403 errors,
and access the shared institution workspace.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from src.api.main import app
from src.db.models import Base
from src.db.session import get_db_session


@pytest_asyncio.fixture
async def multi_user_client(tmp_path):
    test_db_path = tmp_path / "test_multi_user.db"
    test_db_url = f"sqlite+aiosqlite:///{test_db_path}"

    test_engine = create_async_engine(test_db_url, echo=False)
    test_session_factory = async_sessionmaker(
        bind=test_engine, class_=AsyncSession, expire_on_commit=False
    )

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_db_session():
        async with test_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    app.dependency_overrides[get_db_session] = override_get_db_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

    app.dependency_overrides.clear()
    await test_engine.dispose()


@pytest.mark.anyio
async def test_multiple_users_can_onboard_and_use_same_entity(multi_user_client: AsyncClient):
    """
    Test scenario:
    1. User 1 (Principal) signs up and onboards under Entity ID 'RVCE'.
    2. User 2 (Placement Officer) signs up and onboards under the SAME Entity ID 'RVCE'.
    3. User 2 must NOT receive 403 ("already registered to another organization or user").
    4. Both users should have primary_institution_id == 'RVCE' and access the institution.
    """
    # 1. User 1 Signup
    u1_signup = await multi_user_client.post(
        "/api/v1/auth/signup",
        json={
            "email": "principal@rvce.edu.in",
            "password": "Password123!",
            "full_name": "Dr. Principal",
            "job_title": "Principal",
            "skip_otp": True,
        }
    )
    assert u1_signup.status_code == 200, u1_signup.text
    u1_token = u1_signup.json()["access_token"]
    u1_headers = {"Authorization": f"Bearer {u1_token}"}

    # 2. User 1 onboards with entity RVCE
    u1_onboard = await multi_user_client.post(
        "/api/v1/auth/onboarding",
        headers=u1_headers,
        json={
            "entity_id": "RVCE",
            "entity_name": "R.V. College of Engineering",
            "entity_category": "educational_institution",
            "education_entity_type": "College",
            "education_level": "College",
            "ownership_governance": "Private Aided / Unaided",
            "academic_domains": ["Engineering & Technology"],
            "state": "Karnataka",
            "city": "Bengaluru",
        }
    )
    assert u1_onboard.status_code == 200, u1_onboard.text
    u1_data = u1_onboard.json()
    assert u1_data["status"] == "ONBOARDING_COMPLETED"
    assert u1_data["primary_institution_id"] == "RVCE"

    # Verify existing entities listing includes RVCE
    entities_resp = await multi_user_client.get("/api/v1/auth/onboarding/entities", headers=u1_headers)
    assert entities_resp.status_code == 200
    ent_list = entities_resp.json()
    assert any(e["id"] == "RVCE" for e in ent_list)

    # 3. User 2 (Placement Officer) Signup
    u2_signup = await multi_user_client.post(
        "/api/v1/auth/signup",
        json={
            "email": "placement@rvce.edu.in",
            "password": "Password123!",
            "full_name": "Prof. Placement Head",
            "job_title": "Placement Officer",
            "skip_otp": True,
        }
    )
    assert u2_signup.status_code == 200, u2_signup.text
    u2_token = u2_signup.json()["access_token"]
    u2_headers = {"Authorization": f"Bearer {u2_token}"}

    # 4. User 2 onboards with the SAME entity RVCE
    u2_onboard = await multi_user_client.post(
        "/api/v1/auth/onboarding",
        headers=u2_headers,
        json={
            "entity_id": "RVCE",
            "entity_name": "R.V. College of Engineering",
            "entity_category": "educational_institution",
            "education_entity_type": "College",
            "education_level": "College",
            "ownership_governance": "Private Aided / Unaided",
            "academic_domains": ["Engineering & Technology"],
            "state": "Karnataka",
            "city": "Bengaluru",
        }
    )
    # Must succeed with 200, NOT fail with 403
    assert u2_onboard.status_code == 200, f"Expected 200 but got: {u2_onboard.status_code} - {u2_onboard.text}"
    u2_data = u2_onboard.json()
    assert u2_data["status"] == "ONBOARDING_COMPLETED"
    assert u2_data["primary_institution_id"] == "RVCE"

    # 5. Verify User 2 has an active session and access to RVCE
    u2_active_token = u2_data["access_token"]
    u2_active_headers = {"Authorization": f"Bearer {u2_active_token}"}

    me_resp = await multi_user_client.get("/api/v1/auth/me", headers=u2_active_headers)
    assert me_resp.status_code == 200
    me_data = me_resp.json()
    assert me_data["email"] == "placement@rvce.edu.in"
    assert me_data["primary_institution_id"] == "RVCE"
    assert me_data["onboarding_completed"] is True

    # 6. Verify User 2 can list institutions and sees RVCE
    inst_resp = await multi_user_client.get("/api/v1/institutions", headers=u2_active_headers)
    assert inst_resp.status_code == 200
    inst_ids = [inst["id"] for inst in inst_resp.json()]
    assert "RVCE" in inst_ids

    # 7. Verify User 1 still sees RVCE
    u1_active_headers = {"Authorization": f"Bearer {u1_data['access_token']}"}
    inst_resp_u1 = await multi_user_client.get("/api/v1/institutions", headers=u1_active_headers)
    assert inst_resp_u1.status_code == 200
    inst_ids_u1 = [inst["id"] for inst in inst_resp_u1.json()]
    assert "RVCE" in inst_ids_u1

    # 8. Upload a dataset as User 1 and verify both User 1 and User 2 can access dataset metadata
    csv_content = b"department,academic_year,sanctioned_intake,actual_admissions\nCSE,2023,180,180\nECE,2023,180,175\n"
    upload_resp = await multi_user_client.post(
        "/api/v1/ingest/upload",
        headers=u1_active_headers,
        data={"institution_id": "RVCE"},
        files={"file": ("rvce_admissions.csv", csv_content, "text/csv")},
    )
    assert upload_resp.status_code == 200, upload_resp.text
    upload_json = upload_resp.json()
    assert upload_json["status"] == "SUCCESS"

    # User 2 lists datasets for RVCE
    ds_resp_u2 = await multi_user_client.get("/api/v1/ingest/institutions/RVCE/datasets", headers=u2_active_headers)
    assert ds_resp_u2.status_code == 200, ds_resp_u2.text
    datasets_u2 = ds_resp_u2.json()
    assert len(datasets_u2) >= 1
    ds = datasets_u2[0]
    assert ds["filename"] == "rvce_admissions.csv"
    assert ds["dataset_name"] == "rvce_admissions.csv"
    assert ds["format_type"] == "CSV"
    assert ds["ingestion_type"] == "CSV"
    assert ds["total_signals"] > 0
    assert ds["ingested_at"]
    assert "2023" in ds["academic_periods"]
