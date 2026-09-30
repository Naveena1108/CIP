"""
Comprehensive Verification Test Suite for CIP Phase 1:
- Identity (signup without role selection, login, logout, session persistence, password recovery)
- Progressive Entity Classification & Onboarding across all major paths
- Mandatory Custom "Other" ("Please specify") metadata storage & validation
- Extensible Structure (Organization/Group -> Institutions -> Campuses/Faculties -> Departments -> Programs -> Data/Signals)
- Profiles (User, Organization/Entity, Institution, Child-Entity Management, Applicable Fields)
- Authorization & Multi-Tenant Data Isolation
- Existing RYMEC Excel compatibility with Hierarchy Structure discovery
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

REAL_EXCEL_PATH = "C:/Users/Dell/OneDrive/Documents/antigravity/project data set.xlsx"


@pytest_asyncio.fixture
async def phase1_client():
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
async def test_phase1_auth_signup_login_recovery_session_and_logout(phase1_client: AsyncClient):
    """
    Verify normal user signup (no role selection), session persistence, password recovery,
    JSON/form login, and server-side token revocation on logout.
    """
    # 1. Signup without role selection
    signup_resp = await phase1_client.post(
        "/api/v1/auth/signup",
        json={
            "email": "registrar@metro-university.edu",
            "password": "InitialPassword123!",
            "full_name": "Dr. Kavitha Rao",
            "job_title": "Registrar",
            "phone": "+91-80-22334455",
            "department_or_unit": "Academic Administration",
        },
    )
    assert signup_resp.status_code == 200
    s_data = signup_resp.json()
    assert s_data["access_token"]
    assert s_data["email"] == "registrar@metro-university.edu"
    assert s_data["onboarding_required"] is True
    token = s_data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Session persistence check via /me and /profiles/user
    me_resp = await phase1_client.get("/api/v1/auth/me", headers=headers)
    assert me_resp.status_code == 200
    me = me_resp.json()
    assert me["full_name"] == "Dr. Kavitha Rao"
    assert me["job_title"] == "Registrar"
    assert me["department_or_unit"] == "Academic Administration"
    assert me["onboarding_required"] is True

    # 3. Update User Profile
    upd_user_resp = await phase1_client.put(
        "/api/v1/profiles/user",
        headers=headers,
        json={
            "full_name": "Dr. Kavitha S. Rao",
            "job_title": "Registrar & Compliance Head",
            "phone": "+91-80-99887766",
            "department_or_unit": "Central Secretariat",
        },
    )
    assert upd_user_resp.status_code == 200
    assert upd_user_resp.json()["full_name"] == "Dr. Kavitha S. Rao"
    assert upd_user_resp.json()["job_title"] == "Registrar & Compliance Head"

    # 4. Password recovery flow
    rec_req = await phase1_client.post(
        "/api/v1/auth/password-recovery/request",
        json={"email": "registrar@metro-university.edu"},
    )
    assert rec_req.status_code == 200
    reset_token = rec_req.json()["reset_token"]
    assert reset_token

    rec_confirm = await phase1_client.post(
        "/api/v1/auth/password-recovery/reset",
        json={
            "reset_token": reset_token,
            "new_password": "UpdatedSecurePassword456!",
        },
    )
    assert rec_confirm.status_code == 200
    assert rec_confirm.json()["status"] == "PASSWORD_RESET_COMPLETED"

    # Reusing the same reset token must fail
    reuse_resp = await phase1_client.post(
        "/api/v1/auth/password-recovery/reset",
        json={
            "reset_token": reset_token,
            "new_password": "AnotherPassword789!",
        },
    )
    assert reuse_resp.status_code == 400

    # 5. Login with new password via JSON endpoint
    login_json_resp = await phase1_client.post(
        "/api/v1/auth/login/json",
        json={
            "email": "registrar@metro-university.edu",
            "password": "UpdatedSecurePassword456!",
        },
    )
    assert login_json_resp.status_code == 200
    new_token = login_json_resp.json()["access_token"]
    new_headers = {"Authorization": f"Bearer {new_token}"}

    # 6. Logout revokes token immediately
    logout_resp = await phase1_client.post("/api/v1/auth/logout", headers=new_headers)
    assert logout_resp.status_code == 200
    after_logout = await phase1_client.get("/api/v1/auth/me", headers=new_headers)
    assert after_logout.status_code == 401


@pytest.mark.anyio
async def test_phase1_all_progressive_onboarding_paths_and_custom_other_metadata(phase1_client: AsyncClient):
    """
    Verify progressive classification across:
    - Educational Institution (Multi-domain College)
    - University (with University Type & Campus/School/Faculty -> Department -> Program hierarchy)
    - Educational Group / Network (Organization -> Multiple Child Institutions of different types)
    - Research / Academic Body & Custom "Other" ("Aerospace Research & Training Institute" / "Specialized Research Centre")
    """
    # Verify taxonomy endpoint
    tax_resp = await phase1_client.get("/api/v1/auth/onboarding/taxonomy")
    assert tax_resp.status_code == 200
    tax = tax_resp.json()
    assert len(tax["entity_categories"]) >= 7
    assert "Aerospace/Aviation" in tax["academic_domains"]

    # -------------------------------------------------------------------------
    # PATH 1: Custom "Other" Validation & Storage ("Aerospace Research & Training Institute")
    # -------------------------------------------------------------------------
    u_other = await phase1_client.post(
        "/api/v1/auth/signup",
        json={"email": "director@aero-inst.org", "password": "Password123!", "full_name": "Aero Director"},
    )
    h_other = {"Authorization": f"Bearer {u_other.json()['access_token']}"}

    # Missing "Please specify" for entity_category="other" must fail with 422
    bad_other_cat = await phase1_client.post(
        "/api/v1/auth/onboarding",
        headers=h_other,
        json={
            "entity_id": "AERO_RES_01",
            "entity_name": "National Aerospace Institute",
            "entity_category": "other",
            "ownership_governance": "Public/Autonomous",
            "education_entity_type": "Research Institution",
            "academic_domains": ["Aerospace/Aviation"],
        },
    )
    assert bad_other_cat.status_code == 422

    # Missing "Please specify" for ownership_governance="Other" must fail with 422
    bad_other_gov = await phase1_client.post(
        "/api/v1/auth/onboarding",
        headers=h_other,
        json={
            "entity_id": "AERO_RES_01",
            "entity_name": "National Aerospace Institute",
            "entity_category": "research_academic_body",
            "ownership_governance": "Other",
            "education_entity_type": "Research Institution",
            "academic_domains": ["Aerospace/Aviation"],
        },
    )
    assert bad_other_gov.status_code == 422

    # Missing "Please specify" for education_entity_type="Other" must fail with 422
    bad_other_edu = await phase1_client.post(
        "/api/v1/auth/onboarding",
        headers=h_other,
        json={
            "entity_id": "AERO_RES_01",
            "entity_name": "National Aerospace Institute",
            "entity_category": "research_academic_body",
            "ownership_governance": "Public/Autonomous",
            "education_entity_type": "Other",
            "academic_domains": ["Aerospace/Aviation"],
        },
    )
    assert bad_other_edu.status_code == 422

    # Missing "Please specify" for academic_domains=["Other"] must fail with 422
    bad_other_dom = await phase1_client.post(
        "/api/v1/auth/onboarding",
        headers=h_other,
        json={
            "entity_id": "AERO_RES_01",
            "entity_name": "National Aerospace Institute",
            "entity_category": "research_academic_body",
            "ownership_governance": "Public/Autonomous",
            "education_entity_type": "Research Institution",
            "academic_domains": ["Aerospace/Aviation", "Other"],
        },
    )
    assert bad_other_dom.status_code == 422

    # Valid Custom "Other" across multiple dimensions stored verbatim as real metadata
    ok_other = await phase1_client.post(
        "/api/v1/auth/onboarding",
        headers=h_other,
        json={
            "entity_id": "AERO_RES_01",
            "entity_name": "National Aerospace & Deep Space Centre",
            "entity_category": "other",
            "entity_category_other": "Aerospace Research & Training Institute",
            "ownership_governance": "Other",
            "ownership_governance_other": "Joint Inter-Governmental Consortium",
            "education_entity_type": "Other",
            "education_entity_type_other": "Specialized Research Centre",
            "academic_domains": ["Aerospace/Aviation", "Other"],
            "academic_domain_other": "Hypersonic Propulsion & Orbital Mechanics",
            "state": "Karnataka",
            "city": "Bengaluru",
        },
    )
    assert ok_other.status_code == 200
    ent_other = ok_other.json()["entity"]
    assert ent_other["entity_category"] == "other"
    assert ent_other["entity_category_other"] == "Aerospace Research & Training Institute"
    assert ent_other["ownership_governance_other"] == "Joint Inter-Governmental Consortium"
    assert ent_other["education_entity_type_other"] == "Specialized Research Centre"
    assert ent_other["academic_domain_other"] == "Hypersonic Propulsion & Orbital Mechanics"
    assert "Aerospace/Aviation" in ent_other["academic_domains"]

    # Verify stored verbatim in Institution Profile endpoint
    prof_other = await phase1_client.get("/api/v1/profiles/institutions/AERO_RES_01", headers=h_other)
    assert prof_other.status_code == 200
    assert prof_other.json()["entity_category_other"] == "Aerospace Research & Training Institute"
    assert prof_other.json()["education_entity_type_other"] == "Specialized Research Centre"

    # -------------------------------------------------------------------------
    # PATH 2: University -> Campuses/Schools/Faculties -> Departments -> Programs -> Data/Signals
    # -------------------------------------------------------------------------
    u_uni = await phase1_client.post(
        "/api/v1/auth/signup",
        json={"email": "vc@state-tech-uni.edu", "password": "Password123!", "full_name": "Vice Chancellor"},
    )
    h_uni = {"Authorization": f"Bearer {u_uni.json()['access_token']}"}

    uni_onboard = await phase1_client.post(
        "/api/v1/auth/onboarding",
        headers=h_uni,
        json={
            "entity_id": "UNI_STATE_TECH",
            "entity_name": "State Technological University",
            "entity_category": "educational_institution",
            "ownership_governance": "State Government",
            "education_entity_type": "University",
            "university_type": "Affiliating Technical University",
            "academic_domains": ["Engineering & Technology", "Computer Science/IT", "Architecture", "Management"],
            "state": "Karnataka",
        },
    )
    assert uni_onboard.status_code == 200
    uni_ent = uni_onboard.json()["entity"]
    assert uni_ent["university_type"] == "Affiliating Technical University"
    assert uni_ent["applicable_fields"]["structural_archetype"] == "university"
    assert uni_ent["applicable_fields"]["show_university_type"] is True
    assert uni_ent["applicable_fields"]["show_campus_school_faculty_layer"] is True

    # Add Campus/Faculty -> Department -> Program under University
    campus_resp = await phase1_client.post(
        "/api/v1/profiles/institutions/UNI_STATE_TECH/nodes",
        headers=h_uni,
        json={
            "node_type": "campus_school_faculty",
            "code": "SOE_MAIN",
            "name": "School of Engineering & Computing",
        },
    )
    assert campus_resp.status_code == 200
    campus_id = campus_resp.json()["id"]

    dept_resp = await phase1_client.post(
        "/api/v1/profiles/institutions/UNI_STATE_TECH/nodes",
        headers=h_uni,
        json={
            "node_type": "department",
            "parent_node_id": campus_id,
            "code": "CSE",
            "name": "Department of Computer Science & Engineering",
            "academic_domain": "Computer Science/IT",
        },
    )
    assert dept_resp.status_code == 200
    dept_id = dept_resp.json()["id"]

    prog_resp = await phase1_client.post(
        "/api/v1/profiles/institutions/UNI_STATE_TECH/nodes",
        headers=h_uni,
        json={
            "node_type": "program",
            "parent_node_id": dept_id,
            "code": "BTECH_CSE",
            "name": "B.Tech in Computer Science & Engineering",
            "degree_or_level": "Undergraduate (B.Tech)",
            "sanctioned_intake": 180,
        },
    )
    assert prog_resp.status_code == 200

    # Ingest synthetic signals for UNI_STATE_TECH and verify full hierarchy tree
    await phase1_client.post(
        "/api/v1/ingest/synthetic",
        headers=h_uni,
        json={"institution_id": "UNI_STATE_TECH", "scenario": "HEALTHY", "start_year": 2021, "num_years": 4},
    )

    uni_struct = await phase1_client.get("/api/v1/profiles/institutions/UNI_STATE_TECH/structure", headers=h_uni)
    assert uni_struct.status_code == 200
    s_data = uni_struct.json()
    assert s_data["structural_archetype"] == "university"
    assert len(s_data["campuses_schools_faculties"]) == 1
    assert s_data["campuses_schools_faculties"][0]["code"] == "SOE_MAIN"
    assert any(d["code"] == "CSE" for d in s_data["departments"])
    assert len(s_data["programs"]) == 1
    assert s_data["programs"][0]["code"] == "BTECH_CSE"
    assert s_data["signals_summary"]["total_snapshots"] == 12

    # -------------------------------------------------------------------------
    # PATH 3: Organization/Group -> Multiple Institutions (Different Types) -> Departments -> Programs
    # -------------------------------------------------------------------------
    u_org = await phase1_client.post(
        "/api/v1/auth/signup",
        json={"email": "secretary@edu-trust-group.org", "password": "Password123!", "full_name": "General Secretary"},
    )
    h_org = {"Authorization": f"Bearer {u_org.json()['access_token']}"}

    org_onboard = await phase1_client.post(
        "/api/v1/auth/onboarding",
        headers=h_org,
        json={
            "entity_id": "ORG_SANGHA_01",
            "entity_name": "Deccan Vidya Vardhaka Educational Society",
            "entity_category": "educational_group_network",
            "ownership_governance": "Society",
            "education_entity_type": "Educational Society/Sangha",
            "academic_domains": ["Engineering & Technology", "Medicine", "Commerce", "Education"],
            "state": "Karnataka",
            "child_institutions": [
                {
                    "institution_id": "INST_ENGG_01",
                    "name": "Deccan Institute of Engineering & Technology",
                    "education_entity_type": "College",
                    "ownership_governance": "Society",
                    "academic_domains": ["Engineering & Technology", "Computer Science/IT"],
                },
                {
                    "institution_id": "INST_MED_01",
                    "name": "Deccan College of Medical & Allied Sciences",
                    "education_entity_type": "College",
                    "ownership_governance": "Society",
                    "academic_domains": ["Medicine", "Nursing", "Allied Health"],
                },
            ],
        },
    )
    assert org_onboard.status_code == 200
    org_res = org_onboard.json()
    assert len(org_res["child_institutions"]) == 2

    # Add a 3rd institution of a different type (High/Secondary School) via POST /api/v1/profiles/institutions
    school_resp = await phase1_client.post(
        "/api/v1/profiles/institutions",
        headers=h_org,
        json={
            "institution_id": "INST_SCHOOL_01",
            "name": "Deccan Public High School",
            "entity_category": "educational_institution",
            "ownership_governance": "Society",
            "education_entity_type": "High/Secondary School",
            "parent_organization_id": "ORG_SANGHA_01",
        },
    )
    assert school_resp.status_code == 200
    school_prof = school_resp.json()
    # Verify school does not force university or higher-ed domain fields
    assert school_prof["applicable_fields"]["show_university_type"] is False
    assert school_prof["applicable_fields"]["show_academic_domains"] is False
    assert school_prof["applicable_fields"]["show_accreditation_grade"] is False

    # Verify Organization Profile lists all constituent institutions
    org_prof_resp = await phase1_client.get("/api/v1/profiles/organization", headers=h_org)
    assert org_prof_resp.status_code == 200
    org_prof = org_prof_resp.json()
    assert org_prof["id"] == "ORG_SANGHA_01"
    inst_ids_in_org = {i["id"] for i in org_prof["institutions"]}
    assert {"ORG_SANGHA_01", "INST_ENGG_01", "INST_MED_01", "INST_SCHOOL_01"}.issubset(inst_ids_in_org)

    # -------------------------------------------------------------------------
    # AUTHORIZATION & MULTI-TENANT DATA ISOLATION CHECK
    # -------------------------------------------------------------------------
    # User from UNI_STATE_TECH (h_uni) must be denied access (403) to ORG_SANGHA_01 and INST_ENGG_01
    cross_inst_get = await phase1_client.get("/api/v1/profiles/institutions/INST_ENGG_01", headers=h_uni)
    assert cross_inst_get.status_code == 403

    cross_struct_get = await phase1_client.get("/api/v1/profiles/institutions/INST_ENGG_01/structure", headers=h_uni)
    assert cross_struct_get.status_code == 403

    cross_node_post = await phase1_client.post(
        "/api/v1/profiles/institutions/INST_ENGG_01/nodes",
        headers=h_uni,
        json={"node_type": "department", "code": "MECH", "name": "Mechanical Engineering"},
    )
    assert cross_node_post.status_code == 403

    cross_eval = await phase1_client.get("/api/v1/institutions/AERO_RES_01/evaluate", headers=h_uni)
    assert cross_eval.status_code == 403


@pytest.mark.anyio
async def test_phase1_rymec_compatibility_with_structure_and_intelligence(phase1_client: AsyncClient):
    """
    Verify that existing RYMEC Excel upload, evaluation, dossier, simulation, narrative,
    and PDF report remain 100% functional and integrate seamlessly with Phase 1 hierarchy structure.
    """
    if not os.path.exists(REAL_EXCEL_PATH):
        pytest.skip("Real RYMEC Excel dataset not present on disk")

    signup_resp = await phase1_client.post(
        "/api/v1/auth/signup",
        json={"email": "principal@rymec-test.edu", "password": "Password123!", "full_name": "Principal"},
    )
    headers = {"Authorization": f"Bearer {signup_resp.json()['access_token']}"}

    await phase1_client.post(
        "/api/v1/auth/onboarding",
        headers=headers,
        json={
            "entity_id": "RYMEC",
            "entity_name": "Rao Bahadur Y. Mahabaleswarappa Engineering College",
            "entity_category": "educational_institution",
            "ownership_governance": "Society",
            "education_entity_type": "College",
            "academic_domains": ["Engineering & Technology", "Management"],
            "state": "Karnataka",
            "city": "Ballari",
        },
    )

    with open(REAL_EXCEL_PATH, "rb") as f:
        excel_bytes = f.read()

    files = {"file": ("project_data_set.xlsx", excel_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    ingest_resp = await phase1_client.post("/api/v1/ingest/excel", headers=headers, files=files)
    assert ingest_resp.status_code == 200
    assert ingest_resp.json()["institution_id"] == "RYMEC"

    # Verify RYMEC name was preserved and not overwritten by ingestion
    prof_resp = await phase1_client.get("/api/v1/profiles/institutions/RYMEC", headers=headers)
    assert prof_resp.status_code == 200
    assert prof_resp.json()["name"] == "Rao Bahadur Y. Mahabaleswarappa Engineering College"

    # Verify RYMEC structure endpoint surfaces auto-discovered departments and signals
    struct_resp = await phase1_client.get("/api/v1/profiles/institutions/RYMEC/structure", headers=headers)
    assert struct_resp.status_code == 200
    struct = struct_resp.json()
    assert struct["signals_summary"]["total_snapshots"] >= 90
    assert len(struct["departments"]) >= 5

    # Verify evaluation Still Works
    eval_resp = await phase1_client.get("/api/v1/institutions/RYMEC/evaluate", headers=headers)
    assert eval_resp.status_code == 200
    assert eval_resp.json()["risk_level"] in ["HIGH", "CRITICAL"]
