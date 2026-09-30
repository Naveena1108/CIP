"""
CIP Phase 7 Verification Suite:
Organization / Network Intelligence & Cross-Institution Integration.

Verifies:
1. Multi-Institution Organization Hierarchy:
   Organization / Educational Group -> multiple institutions (school, PUC, college, engineering, medical)
   -> departments/programs -> data/signals.
   No hardcoding of V.V. Sangha or RYMEC.
2. Views:
   - Institution view
   - Organization/network view (institution count, data coverage, institution-level risks,
     common/emerging patterns, cross-institution comparisons where evidence permits).
3. Non-Causal Epistemic Discipline:
   - Do not infer a shared cause merely because multiple institutions show similar changes.
4. Access Control:
   - Respect user, organization, institution, and department/program permissions.
5. End-to-End Organization Context Integration:
   - Verify organization context flows through authentication, profiles, data ingestion,
     signals, intelligence, evidence, predictions, memory, and reports.
   - Verify RYMEC still works as an ordinary institution inside this model.
"""

import io
import os
import pytest
from fastapi.testclient import TestClient

from src.api.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _signup_and_get_headers(client: TestClient, email: str, full_name: str, dept: str = "Governance") -> dict:
    res = client.post(
        "/api/v1/auth/signup",
        json={
            "email": email,
            "password": "SecurePassword#2026",
            "full_name": full_name,
            "job_title": "Network Administrator",
            "department_or_unit": dept,
        },
    )
    if res.status_code in (200, 201):
        token = res.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}
    login_res = client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": "SecurePassword#2026"},
    )
    assert login_res.status_code == 200, login_res.text
    token = login_res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _ingest_multi_year_signals(
    client: TestClient,
    headers: dict,
    institution_id: str,
    department: str,
    years_and_metrics: list,
):
    """
    Helper to ingest Admissions, Placements, and CET Ranking signals across multiple academic years.
    years_and_metrics: list of tuples (year, intake, admitted, placed_pct, closing_rank)
    """
    for yr, intake, admitted, placed_pct, closing_rank in years_and_metrics:
        # Admissions signal
        r1 = client.post(
            "/api/v1/ingest/data",
            headers=headers,
            json={
                "signal_type": "admissions",
                "payload": {
                    "institution_id": institution_id,
                    "academic_year": yr,
                    "department": department,
                    "sanctioned_intake": intake,
                    "admitted_students": admitted,
                    "dropout_count": max(0, int((intake - admitted) * 0.05)),
                },
            },
        )
        assert r1.status_code == 201, r1.text

        # Placements signal
        graduating = max(10, admitted)
        placed = max(0, min(graduating, int(round(graduating * (placed_pct / 100.0)))))
        r2 = client.post(
            "/api/v1/ingest/data",
            headers=headers,
            json={
                "signal_type": "placements",
                "payload": {
                    "institution_id": institution_id,
                    "academic_year": yr,
                    "department": department,
                    "graduating_cohort_size": graduating,
                    "placed_students": placed,
                    "median_salary_lpa": 5.2 if placed_pct >= 65 else 3.8,
                },
            },
        )
        assert r2.status_code == 201, r2.text

        # CET Ranking signal
        r3 = client.post(
            "/api/v1/ingest/data",
            headers=headers,
            json={
                "signal_type": "cet_ranking",
                "payload": {
                    "institution_id": institution_id,
                    "academic_year": yr,
                    "department": department,
                    "opening_rank": max(100, closing_rank - 8000),
                    "closing_rank": closing_rank,
                    "category": "GM",
                    "round_number": 2,
                },
            },
        )
        assert r3.status_code == 201, r3.text


def test_phase7_end_to_end_multi_institution_organization_and_network_intelligence(client: TestClient, monkeypatch):
    """
    Comprehensive end-to-end verification of CIP Phase 7:
    - Onboarding a realistic Educational Group with 5 heterogeneous institution types
      (school, PUC, college, engineering, medical) + departments & programs
    - Context switching across institutions and view modes
    - Organization aggregation (institution count, data coverage, institution-level risks)
    - Non-causal cross-institution pattern observation (OBSERVED_CO_OCCURRENCE, shared_cause_inferred=False)
    - Cross-institution comparisons where evidence permits (and explicit exclusions where not)
    - Organization context flowing through auth, profiles, ingestion, signals, intelligence,
      evidence provenance, predictions, memory, and reports.
    """
    # Force deterministic fallback during test so tests run fast and deterministically
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("GROQ_API_KEY", "")

    headers = _signup_and_get_headers(
        client,
        email="chancellor@vidyanetwork.org",
        full_name="Dr. Arundhati Deshmukh",
        dept="Group Executive Council",
    )

    # 1. Onboard Educational Group / Organization
    org_id = "ORG_VIDYA_NET_01"
    onb_res = client.post(
        "/api/v1/auth/onboarding",
        headers=headers,
        json={
            "entity_id": org_id,
            "entity_name": "Vidya Bharati Multi-Institution Educational Network",
            "entity_category": "educational_group_network",
            "ownership_governance": "Trust",
            "education_entity_type": "Education Network",
            "academic_domains": ["Engineering & Technology", "Medicine", "Science", "Commerce"],
            "state": "Karnataka",
            "city": "Dharwad",
        },
    )
    assert onb_res.status_code == 200, onb_res.text
    assert onb_res.json()["organization_id"] == org_id

    # 2. Provision 5 heterogeneous constituent institution types under ORG_VIDYA_NET_01:
    #    school, PUC, college, engineering, medical
    constituent_specs = [
        {
            "institution_id": "INST_NET_SCHOOL_01",
            "name": "Vidya Bharati Public High School",
            "education_entity_type": "High/Secondary School",
            "ownership_governance": "Trust",
            "academic_domain": "Multidisciplinary",
            "departments": [("STD_10", "Secondary Grade 10 Division"), ("STD_9", "Secondary Grade 9 Division")],
            "programs": [],
        },
        {
            "institution_id": "INST_NET_PUC_01",
            "name": "Vidya Bharati Pre-University Science & Commerce College",
            "education_entity_type": "PUC/Pre-University",
            "ownership_governance": "Trust",
            "academic_domain": "Science",
            "departments": [("PCMB", "Physics Chemistry Maths Biology Stream"), ("PCMC", "Physics Chemistry Maths Computer Stream")],
            "programs": [("PUC_SCI", "Pre-University Science Certificate", "PCMB")],
        },
        {
            "institution_id": "INST_NET_DEGREE_01",
            "name": "Vidya Bharati College of Arts & Commerce",
            "education_entity_type": "College",
            "ownership_governance": "Trust",
            "academic_domain": "Commerce",
            "departments": [("COMMERCE", "Department of Commerce"), ("ECONOMICS", "Department of Economics")],
            "programs": [("BCOM_HONS", "B.Com Honours", "COMMERCE")],
        },
        {
            "institution_id": "INST_NET_ENGG_01",
            "name": "Vidya Bharati Institute of Engineering & Technology",
            "education_entity_type": "College",
            "ownership_governance": "Trust",
            "academic_domain": "Engineering & Technology",
            "departments": [("CSE", "Computer Science & Engineering"), ("MECH", "Mechanical Engineering")],
            "programs": [("BTECH_CSE", "B.E. Computer Science", "CSE"), ("BTECH_MECH", "B.E. Mechanical Engineering", "MECH")],
        },
        {
            "institution_id": "INST_NET_MED_01",
            "name": "Vidya Bharati Institute of Medical Sciences",
            "education_entity_type": "College",
            "ownership_governance": "Trust",
            "academic_domain": "Medicine",
            "departments": [("GEN_MED", "General Medicine & Clinical Practice"), ("SURGERY", "Department of Surgery")],
            "programs": [("MBBS", "Bachelor of Medicine and Surgery", "GEN_MED")],
        },
    ]

    for spec in constituent_specs:
        c_res = client.post(
            "/api/v1/profiles/institutions",
            headers=headers,
            json={
                "institution_id": spec["institution_id"],
                "name": spec["name"],
                "education_entity_type": spec["education_entity_type"],
                "ownership_governance": spec["ownership_governance"],
                "academic_domain": spec["academic_domain"],
                "state": "Karnataka",
                "city": "Dharwad",
            },
        )
        assert c_res.status_code in (200, 201), c_res.text
        created_inst = c_res.json()
        assert created_inst["organization_id"] == org_id

        # Create departments and programs under each constituent institution
        dept_node_ids = {}
        for d_code, d_name in spec["departments"]:
            d_res = client.post(
                f"/api/v1/profiles/institutions/{spec['institution_id']}/hierarchy",
                headers=headers,
                json={
                    "node_type": "department",
                    "code": d_code,
                    "name": d_name,
                    "sanctioned_intake": 120,
                },
            )
            assert d_res.status_code in (200, 201), d_res.text
            dept_node_ids[d_code] = d_res.json()["id"]

        for p_code, p_name, parent_dept_code in spec["programs"]:
            p_res = client.post(
                f"/api/v1/profiles/institutions/{spec['institution_id']}/hierarchy",
                headers=headers,
                json={
                    "node_type": "program",
                    "code": p_code,
                    "name": p_name,
                    "parent_node_id": dept_node_ids.get(parent_dept_code),
                    "degree_or_level": "UG",
                    "sanctioned_intake": 60,
                },
            )
            assert p_res.status_code in (200, 201), p_res.text

    # 3. Verify Context Switching across constituent institutions and view modes
    sw_res = client.post(
        "/api/v1/profiles/switch-context",
        headers=headers,
        json={"institution_id": "INST_NET_ENGG_01", "view_mode": "institution"},
    )
    assert sw_res.status_code == 200, sw_res.text
    sw_body = sw_res.json()
    assert sw_body["active_institution_id"] == "INST_NET_ENGG_01"
    assert sw_body["active_organization_id"] == org_id
    assert sw_body["view_mode"] == "institution"

    me_res = client.get("/api/v1/auth/me", headers=headers)
    assert me_res.status_code == 200
    me_data = me_res.json()
    assert me_data["organization_id"] == org_id
    assert me_data["primary_institution_id"] == "INST_NET_ENGG_01"
    assert me_data["access_policy"]["access_scope_level"] == "organization"

    # 4. Ingest multi-year signals into constituent institutions:
    #    - INST_NET_ENGG_01: 4 years (2021-2024) showing steep placement drop & rising vacancy in CSE & MECH
    _ingest_multi_year_signals(
        client,
        headers,
        institution_id="INST_NET_ENGG_01",
        department="CSE",
        years_and_metrics=[
            (2021, 120, 118, 88.0, 14000),
            (2022, 120, 115, 84.0, 16500),
            (2023, 120, 98, 68.0, 24000),
            (2024, 120, 72, 42.0, 48000),
        ],
    )
    _ingest_multi_year_signals(
        client,
        headers,
        institution_id="INST_NET_ENGG_01",
        department="MECH",
        years_and_metrics=[
            (2021, 120, 110, 78.0, 28000),
            (2022, 120, 104, 74.0, 32000),
            (2023, 120, 82, 58.0, 45000),
            (2024, 120, 54, 35.0, 72000),
        ],
    )

    #    - INST_NET_MED_01: 4 years (2021-2024) ALSO showing placement/internship drop & intake pressure
    #      so that the engine detects a cross-institution co-occurring pattern without inferring a shared cause!
    _ingest_multi_year_signals(
        client,
        headers,
        institution_id="INST_NET_MED_01",
        department="GEN_MED",
        years_and_metrics=[
            (2021, 150, 150, 92.0, 8000),
            (2022, 150, 148, 89.0, 9500),
            (2023, 150, 130, 71.0, 18000),
            (2024, 150, 95, 44.0, 39000),
        ],
    )

    #    - INST_NET_PUC_01: 3 years (2022-2024) of healthy/stable signals
    _ingest_multi_year_signals(
        client,
        headers,
        institution_id="INST_NET_PUC_01",
        department="PCMB",
        years_and_metrics=[
            (2022, 200, 196, 91.0, 5000),
            (2023, 200, 195, 92.0, 4900),
            (2024, 200, 198, 93.0, 4800),
        ],
    )

    #    - INST_NET_SCHOOL_01: 1 year (2024) of sparse CSV upload via Universal Ingestion Pipeline
    school_csv = (
        "academic_year,department,pass_percentage,attendance_rate,student_retention_pct\n"
        "2024,STD_10,96.4,94.2,98.5\n"
    ).encode("utf-8")
    up_res = client.post(
        "/api/v1/ingest/upload",
        headers=headers,
        data={"institution_id": "INST_NET_SCHOOL_01"},
        files={"file": ("school_board_results_2024.csv", io.BytesIO(school_csv), "text/csv")},
    )
    assert up_res.status_code == 200, up_res.text
    assert up_res.json()["institution_id"] == "INST_NET_SCHOOL_01"

    #    - Note: INST_NET_DEGREE_01 is intentionally left with 0 signals to test partial coverage & comparison exclusions.

    # 5. Evaluate Organization / Network Intelligence View
    org_intel_res = client.get(f"/api/v1/organizations/{org_id}/intelligence", headers=headers)
    assert org_intel_res.status_code == 200, org_intel_res.text
    org_report = org_intel_res.json()

    assert org_report["organization_id"] == org_id
    assert org_report["view_mode"] == "organization_network_view"
    assert org_report["institution_count"] == 5

    # Verify heterogeneous institution types breakdown
    brk = org_report["institution_breakdown"]
    assert brk["total_institutions"] == 5
    assert brk["with_ingested_data"] == 4
    assert brk["without_data"] == 1
    by_type = brk["by_type"]
    assert by_type.get("school", 0) >= 1
    assert by_type.get("puc", 0) >= 1
    assert by_type.get("engineering", 0) >= 1
    assert by_type.get("medical", 0) >= 1
    assert by_type.get("college", 0) >= 1

    # Verify organization-level data coverage
    cov = org_report["data_coverage"]
    assert cov["institutions_with_any_data"] == 4
    assert cov["institutions_with_multi_year_history"] == 3
    assert cov["overall_coverage_pct"] == 80.0
    assert "admissions" in cov["domains_covered_across_org"]
    assert "placements" in cov["domains_covered_across_org"]
    assert any("INST_NET_DEGREE_01" in gap for gap in cov["coverage_gaps"])
    assert any("INST_NET_SCHOOL_01" in gap for gap in cov["coverage_gaps"])

    # Verify institution-level risks & department/program rollups
    inst_risks = {r["institution_id"]: r for r in org_report["institution_level_risks"]}
    assert set(inst_risks.keys()) == {
        "INST_NET_SCHOOL_01",
        "INST_NET_PUC_01",
        "INST_NET_DEGREE_01",
        "INST_NET_ENGG_01",
        "INST_NET_MED_01",
    }
    engg_summary = inst_risks["INST_NET_ENGG_01"]
    assert engg_summary["institution_type"] == "engineering"
    assert engg_summary["has_data"] is True
    assert engg_summary["composite_risk_index"] > 0.35
    assert engg_summary["total_anomalies"] >= 1
    dept_codes = {d["code"] for d in engg_summary["departments_and_programs"]}
    assert {"CSE", "MECH", "BTECH_CSE", "BTECH_MECH"}.issubset(dept_codes)

    degree_summary = inst_risks["INST_NET_DEGREE_01"]
    assert degree_summary["has_data"] is False
    assert degree_summary["risk_stage"] == "Observation"

    # 6. Verify Cross-Institution Observation (Strictly Non-Causal)
    patterns = org_report["common_emerging_patterns"]
    assert len(patterns) >= 1, "Expected at least one co-occurring pattern across INST_NET_ENGG_01 and INST_NET_MED_01"
    for pat in patterns:
        assert pat["epistemic_classification"] == "OBSERVED_CO_OCCURRENCE"
        assert pat["shared_cause_inferred"] is False
        assert "not" in pat["non_causal_explanation"].lower() or "co-occurrence" in pat["non_causal_explanation"].lower()
        assert len(pat["institutions_involved"]) >= 2

    # 7. Verify Cross-Institution Comparisons (Where Evidence Permits)
    comparisons = org_report["cross_institution_comparisons"]
    assert len(comparisons) >= 3
    cri_comp = next(c for c in comparisons if c["metric_key"] == "composite_risk_index")
    assert cri_comp["comparable_evidence_permits"] is True
    assert "INST_NET_DEGREE_01" in cri_comp["institutions_excluded"]
    assert any("INST_NET_DEGREE_01" in reason for reason in cri_comp["exclusion_reasons"])

    # 8. Verify Organization Context Flows Through All Subsystems:
    #    - Signals
    org_sig_res = client.get(f"/api/v1/organizations/{org_id}/signals", headers=headers)
    assert org_sig_res.status_code == 200
    assert org_sig_res.json()["organization_id"] == org_id
    assert org_sig_res.json()["institution_count"] == 5

    #    - Single-Institution Evaluate, Intelligence, Evidence Provenance, Forecast, Memory, Report
    ev_res = client.get("/api/v1/institutions/INST_NET_ENGG_01/evaluate", headers=headers)
    assert ev_res.status_code == 200
    assert ev_res.json()["organization_id"] == org_id

    in_res = client.get("/api/v1/institutions/INST_NET_ENGG_01/intelligence", headers=headers)
    assert in_res.status_code == 200
    assert in_res.json()["organization_id"] == org_id

    prov_res = client.get("/api/v1/institutions/INST_NET_ENGG_01/evidence-provenance", headers=headers)
    assert prov_res.status_code == 200
    assert prov_res.json()["organization_id"] == org_id
    assert prov_res.json()["total_provenance_records"] >= 1

    org_prov_res = client.get(f"/api/v1/organizations/{org_id}/evidence-provenance", headers=headers)
    assert org_prov_res.status_code == 200
    org_prov = org_prov_res.json()
    assert org_prov["organization_id"] == org_id
    assert org_prov["institutions_with_evidence"] >= 3
    assert org_prov["total_provenance_records"] >= prov_res.json()["total_provenance_records"]

    fc_res = client.get("/api/v1/institutions/INST_NET_ENGG_01/forecast", headers=headers)
    assert fc_res.status_code == 200
    assert fc_res.json()["organization_id"] == org_id
    assert fc_res.json()["status"] == "SUFFICIENT_EVIDENCE"

    # Sparse institution (INST_NET_SCHOOL_01 with 1 year) returns INSUFFICIENT_EVIDENCE in organization forecast
    org_fc_res = client.get(f"/api/v1/organizations/{org_id}/forecast", headers=headers)
    assert org_fc_res.status_code == 200
    org_fc = org_fc_res.json()
    assert org_fc["organization_id"] == org_id
    assert org_fc["sufficient_evidence_forecasts"] == 3
    assert org_fc["insufficient_evidence_forecasts"] == 2

    mem_res = client.get("/api/v1/institutions/INST_NET_ENGG_01/memory", headers=headers)
    assert mem_res.status_code == 200
    assert mem_res.json()["organization_id"] == org_id

    org_mem_res = client.get(f"/api/v1/organizations/{org_id}/memory", headers=headers)
    assert org_mem_res.status_code == 200
    assert org_mem_res.json()["organization_id"] == org_id
    assert org_mem_res.json()["total_entries"] >= mem_res.json()["total_entries"]

    rep_res = client.get("/api/v1/institutions/INST_NET_ENGG_01/report", headers=headers)
    assert rep_res.status_code == 200
    assert rep_res.json()["organization_id"] == org_id

    org_rep_res = client.get(f"/api/v1/organizations/{org_id}/report", headers=headers)
    assert org_rep_res.status_code == 200
    assert org_rep_res.json()["organization_id"] == org_id
    assert len(org_rep_res.json()["institution_reports"]) >= 3


def test_phase7_data_isolation_and_four_tier_access_permissions(client: TestClient, monkeypatch):
    """
    Verifies:
    1. Cross-organization data isolation (Rival Organization cannot access Vidya Network or its institutions).
    2. Institution-level permission scope (Principal of PUC College can access PUC College,
       but is forbidden from sibling Engineering College and Organization Network View).
    3. Department/Program-level permission scope (HOD of CSE at Engineering College can access
       CSE signals/intelligence/ingestion, but is forbidden from MECH department ingestion and
       does not see MECH signals in evaluations or intelligence).
    """
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("GROQ_API_KEY", "")

    # Admin of ORG_VIDYA_NET_01 (already created in previous test, or sign in)
    login_res = client.post(
        "/api/v1/auth/login",
        data={"username": "chancellor@vidyanetwork.org", "password": "SecurePassword#2026"},
    )
    assert login_res.status_code == 200
    org_admin_headers = {"Authorization": f"Bearer {login_res.json()['access_token']}"}
    org_id = "ORG_VIDYA_NET_01"

    # 1. Rival Organization User -> Strictly isolated from ORG_VIDYA_NET_01
    rival_headers = _signup_and_get_headers(
        client,
        email="admin@rivaltrust.org",
        full_name="Rival Trust Director",
    )
    rival_onb = client.post(
        "/api/v1/auth/onboarding",
        headers=rival_headers,
        json={
            "entity_id": "ORG_RIVAL_TRUST_99",
            "entity_name": "Rival Educational Trust",
            "entity_category": "educational_group_network",
            "ownership_governance": "Trust",
            "education_entity_type": "Education Network",
            "academic_domains": ["Engineering & Technology"],
            "state": "Karnataka",
        },
    )
    assert rival_onb.status_code == 200

    # Rival cannot access ORG_VIDYA_NET_01 network intelligence or INST_NET_ENGG_01
    assert client.get(f"/api/v1/organizations/{org_id}/intelligence", headers=rival_headers).status_code == 403
    assert client.get("/api/v1/institutions/INST_NET_ENGG_01/evaluate", headers=rival_headers).status_code == 403
    assert client.get("/api/v1/institutions/INST_NET_ENGG_01/intelligence", headers=rival_headers).status_code == 403
    assert client.get("/api/v1/institutions/INST_NET_ENGG_01/evidence-provenance", headers=rival_headers).status_code == 403

    # 2. Institution-Scoped User (Principal of INST_NET_PUC_01 only)
    puc_principal_headers = _signup_and_get_headers(
        client,
        email="principal.puc@vidyanetwork.org",
        full_name="Prof. K. Kulkarni",
        dept="PUC Administration",
    )
    assign_puc = client.post(
        "/api/v1/profiles/permissions",
        headers=org_admin_headers,
        json={
            "target_email": "principal.puc@vidyanetwork.org",
            "access_scope_level": "institution",
            "allowed_organization_ids": [org_id],
            "allowed_institution_ids": ["INST_NET_PUC_01"],
            "allowed_departments_by_institution": {},
        },
    )
    assert assign_puc.status_code == 200, assign_puc.text
    assert assign_puc.json()["access_scope_level"] == "institution"

    # PUC Principal can access INST_NET_PUC_01
    assert client.get("/api/v1/institutions/INST_NET_PUC_01/evaluate", headers=puc_principal_headers).status_code == 200
    assert client.get("/api/v1/institutions/INST_NET_PUC_01/intelligence", headers=puc_principal_headers).status_code == 200

    # PUC Principal CANNOT access sibling institution INST_NET_ENGG_01 or organization-wide network view
    assert client.get("/api/v1/institutions/INST_NET_ENGG_01/evaluate", headers=puc_principal_headers).status_code == 403
    assert client.get("/api/v1/institutions/INST_NET_ENGG_01/intelligence", headers=puc_principal_headers).status_code == 403
    assert client.get(f"/api/v1/organizations/{org_id}/intelligence", headers=puc_principal_headers).status_code == 403

    # 3. Department/Program-Scoped User (HOD of CSE at INST_NET_ENGG_01 only)
    cse_hod_headers = _signup_and_get_headers(
        client,
        email="hod.cse@vidyanetwork.org",
        full_name="Dr. S. Patil",
        dept="CSE",
    )
    assign_cse = client.post(
        "/api/v1/profiles/permissions",
        headers=org_admin_headers,
        json={
            "target_email": "hod.cse@vidyanetwork.org",
            "access_scope_level": "department_program",
            "allowed_organization_ids": [org_id],
            "allowed_institution_ids": ["INST_NET_ENGG_01"],
            "allowed_departments_by_institution": {"INST_NET_ENGG_01": ["CSE", "BTECH_CSE"]},
        },
    )
    assert assign_cse.status_code == 200, assign_cse.text
    assert assign_cse.json()["access_scope_level"] == "department_program"

    # CSE HOD can view structure of INST_NET_ENGG_01, but only sees CSE department (not MECH)
    struct_res = client.get("/api/v1/profiles/institutions/INST_NET_ENGG_01/structure", headers=cse_hod_headers)
    assert struct_res.status_code == 200
    dept_codes_visible = [d["code"] for d in struct_res.json()["departments"]]
    assert "CSE" in dept_codes_visible
    assert "MECH" not in dept_codes_visible

    # CSE HOD CANNOT ingest data for MECH (403 Forbidden), but CAN ingest data for CSE (201 Created)
    forbidden_ingest = client.post(
        "/api/v1/ingest/data",
        headers=cse_hod_headers,
        json={
            "signal_type": "admissions",
            "payload": {
                "institution_id": "INST_NET_ENGG_01",
                "academic_year": 2025,
                "department": "MECH",
                "sanctioned_intake": 120,
                "admitted_students": 60,
                "dropout_count": 2,
            },
        },
    )
    assert forbidden_ingest.status_code == 403

    allowed_ingest = client.post(
        "/api/v1/ingest/data",
        headers=cse_hod_headers,
        json={
            "signal_type": "admissions",
            "payload": {
                "institution_id": "INST_NET_ENGG_01",
                "academic_year": 2025,
                "department": "CSE",
                "sanctioned_intake": 120,
                "admitted_students": 85,
                "dropout_count": 2,
            },
        },
    )
    assert allowed_ingest.status_code == 201

    # CSE HOD intelligence report only includes CSE baselines, never MECH baselines
    hod_intel_res = client.get("/api/v1/institutions/INST_NET_ENGG_01/intelligence", headers=cse_hod_headers)
    assert hod_intel_res.status_code == 200
    baseline_depts = {b["department"] for b in hod_intel_res.json()["baselines"] if b.get("department")}
    assert "MECH" not in baseline_depts


def test_phase7_rymec_works_as_ordinary_institution_without_hardcoding(client: TestClient, monkeypatch):
    """
    Verifies that:
    1. RYMEC works as an ordinary constituent institution inside an organization model
       (with no special-case hardcoding in engine or API routes).
    2. The UI and Phase 7 engine contain zero hardcoded 'V.V. Sangha' or 'RYMEC' assumptions.
    """
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("GROQ_API_KEY", "")

    # Verify engine and route source files do not hardcode RYMEC or V.V. Sangha logic
    engine_path = os.path.join("src", "engine", "organization_intelligence.py")
    with open(engine_path, "r", encoding="utf-8") as f:
        engine_src = f.read()
    assert "RYMEC" not in engine_src.split('"""')[-1], "organization_intelligence.py must not hardcode RYMEC"
    assert "V.V. Sangha" not in engine_src.split('"""')[-1], "organization_intelligence.py must not hardcode V.V. Sangha"

    # Onboard an organization and add RYMEC as an ordinary engineering institution alongside another college
    headers = _signup_and_get_headers(
        client,
        email="governance@regional-trust.edu",
        full_name="Regional Trust Secretary",
    )
    org_res = client.post(
        "/api/v1/auth/onboarding",
        headers=headers,
        json={
            "entity_id": "ORG_REGIONAL_EDU_01",
            "entity_name": "Regional Education Society",
            "entity_category": "educational_group_network",
            "ownership_governance": "Society",
            "education_entity_type": "Educational Society/Sangha",
            "academic_domains": ["Engineering & Technology", "Science"],
            "state": "Karnataka",
            "city": "Ballari",
        },
    )
    assert org_res.status_code == 200

    # Add RYMEC as an ordinary institution
    rymec_res = client.post(
        "/api/v1/profiles/institutions",
        headers=headers,
        json={
            "institution_id": "RYMEC",
            "name": "Rao Bahadur Y. Mahabaleswarappa Engineering College",
            "education_entity_type": "College",
            "ownership_governance": "Society",
            "academic_domain": "Engineering & Technology",
            "state": "Karnataka",
            "city": "Ballari",
        },
    )
    assert rymec_res.status_code in (200, 201)
    assert rymec_res.json()["organization_id"] == "ORG_REGIONAL_EDU_01"

    _ingest_multi_year_signals(
        client,
        headers,
        institution_id="RYMEC",
        department="CSE",
        years_and_metrics=[
            (2022, 120, 118, 86.0, 18000),
            (2023, 120, 112, 79.0, 22000),
            (2024, 120, 95, 64.0, 31000),
        ],
    )

    # Verify RYMEC works through single-institution evaluation, intelligence, forecast, and organization network view
    eval_res = client.get("/api/v1/institutions/RYMEC/evaluate", headers=headers)
    assert eval_res.status_code == 200
    assert eval_res.json()["institution_id"] == "RYMEC"
    assert eval_res.json()["organization_id"] == "ORG_REGIONAL_EDU_01"

    intel_res = client.get("/api/v1/institutions/RYMEC/intelligence", headers=headers)
    assert intel_res.status_code == 200
    assert intel_res.json()["institution_id"] == "RYMEC"
    assert intel_res.json()["organization_id"] == "ORG_REGIONAL_EDU_01"

    net_res = client.get("/api/v1/organizations/ORG_REGIONAL_EDU_01/intelligence", headers=headers)
    assert net_res.status_code == 200
    net_data = net_res.json()
    assert net_data["institution_count"] == 1
    assert net_data["institution_level_risks"][0]["institution_id"] == "RYMEC"
    assert net_data["institution_level_risks"][0]["institution_type"] == "engineering"

    # Verify UI serves both Institution View and Organization / Network View controls
    dash_res = client.get("/dashboard/")
    assert dash_res.status_code == 200
    html = dash_res.text
    assert "viewModeInstitutionBtn" in html
    assert "viewModeOrganizationBtn" in html
    assert "organizationNetworkViewPanel" in html
    assert "OBSERVED CO-OCCURRENCE" in html
