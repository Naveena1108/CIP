"""
CIP FINAL DEMO GATE — End-to-End Verification Suite
====================================================
Verifies:
1. Obsolete Concept Audit (User-facing HTML + PDF generator)
2. Trust Audit & Data Honesty Audit (7-field provenance, explainable predictions, epistemic separation, sparse data honesty, live vs fallback transparency)
3. Auth & Security Isolation (401 unauthenticated, 403 role/scope, user/org/institution/department isolation, file upload limits, token revocation on logout)
4. Full 21-Step Real-Browser User Journey in Google Chrome via Playwright:
   Signup -> Login -> Entity onboarding/classification -> Profile -> Organization/institution setup ->
   Add Data -> Processing -> Signal discovery -> Overview -> Insights -> Risks -> Investigation ->
   Evidence -> Prediction -> Why prediction changed -> What-If Analysis -> Institutional Memory ->
   What Changed -> Report export -> Logout -> Login again
"""

import io
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML_PATH = REPO_ROOT / "src" / "frontend" / "static" / "index.html"
PDF_GEN_PATH = REPO_ROOT / "src" / "reporting" / "pdf_generator.py"
ARTIFACT_DIR = Path(r"C:\Users\Dell\.gemini\antigravity\brain\bb1f7104-d4de-4230-b024-32f0a5e6559b")


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_final_gate_obsolete_concept_audit():
    """
    Search user-facing code (index.html and pdf_generator.py) for obsolete concepts:
    - AI-CRISS
    - cockpit_operator
    - SuperAdmin-as-user-flow
    - Upload RYMEC
    - Forward Trajectory Simulator
    - Grounded Executive Briefing
    - engineering scenario controls (triggerSyntheticRun, color palette selector)
    - fake/static insights
    """
    html = INDEX_HTML_PATH.read_text(encoding="utf-8")
    pdf_code = PDF_GEN_PATH.read_text(encoding="utf-8")

    forbidden_ui_phrases = [
        "AI-CRISS",
        "cockpit_operator",
        "Upload RYMEC",
        "Forward Trajectory Simulator",
        "Grounded Executive Briefing",
        "triggerSyntheticRun",
        "Color Palette",
        "paletteSelector",
    ]
    for phrase in forbidden_ui_phrases:
        assert phrase not in html, f"Obsolete concept '{phrase}' found in index.html"
        assert phrase not in pdf_code, f"Obsolete concept '{phrase}' found in pdf_generator.py"

    # Verify Signup and Login forms do not expose SuperAdmin or role selection in user flow
    signup_match = re.search(r'<form id="signupForm".*?</form>', html, flags=re.DOTALL)
    assert signup_match is not None
    signup_html = signup_match.group(0)
    assert "SuperAdmin" not in signup_html
    assert "<select" not in signup_html

    # Verify themes are strictly Light, Dark, System Default
    theme_match = re.search(r'<select id="themeSelector".*?</select>', html, flags=re.DOTALL)
    assert theme_match is not None
    theme_options = re.findall(r'<option value="([^"]+)"', theme_match.group(0))
    assert set(theme_options) == {"system", "light", "dark"}


def test_final_gate_trust_and_data_honesty_audit():
    """
    Verify Trust Audit & Data Honesty:
    - Sparse dataset (0 or 1 period) never fabricates predictions, baselines, or fake confidence.
    - Multi-year dataset produces insights with 7-field provenance, explainable predictions,
      and strict OBSERVED_FACT vs INFERENCE separation.
    - Missing datasets/domains surface explicit limitations.
    - AI Executive Analysis and Investigations honestly distinguish live provider vs deterministic fallback.
    """
    run_tag = uuid.uuid4().hex[:6].upper()
    inst_id = f"INST_TRUST_{run_tag}"
    email = f"trust_auditor_{run_tag.lower()}@cip-audit.org"

    with TestClient(app) as client:
        # 1. Sign up and onboard a new institution
        signup_res = client.post(
            "/api/v1/auth/signup",
            json={
                "email": email,
                "password": "TrustPassword123!",
                "full_name": "Dr. Trust Auditor",
                "job_title": "Dean of Quality",
                "department_or_unit": "IQAC",
            },
        )
        assert signup_res.status_code in (200, 201)
        token = signup_res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        onboard_res = client.post(
            "/api/v1/auth/onboarding",
            headers=headers,
            json={
                "entity_id": inst_id,
                "entity_name": "Trust Audit Engineering Institute",
                "entity_category": "educational_institution",
                "ownership_governance": "Private",
                "education_entity_type": "College",
                "academic_domains": ["Engineering & Technology"],
                "state": "Karnataka",
                "city": "Bengaluru",
            },
        )
        assert onboard_res.status_code == 200

        # 2. Sparse check (0 signals): Forecast must return INSUFFICIENT_EVIDENCE, never a fake forecast
        fc_zero = client.get(f"/api/v1/institutions/{inst_id}/forecast", headers=headers)
        assert fc_zero.status_code == 200
        fc_zero_data = fc_zero.json()
        assert fc_zero_data["status"] == "INSUFFICIENT_EVIDENCE"
        assert fc_zero_data["prediction"] is None
        assert len(fc_zero_data["limitations"]) >= 1
        assert fc_zero_data["insufficient_evidence_reason"] is not None

        # 3. Single-period check (1 academic year): Forecast must still refuse to extrapolate
        csv_1yr = (
            "academic_year,department,sanctioned_intake,enrolled_count,vacancy_count,vacancy_rate,dropouts_year_1\n"
            "2023,CSE,120,110,10,0.0833,2\n"
        )
        up_1yr = client.post(
            "/api/v1/ingest/upload",
            headers=headers,
            files={"file": ("admissions_2023.csv", io.BytesIO(csv_1yr.encode("utf-8")), "text/csv")},
            data={"institution_id": inst_id},
        )
        assert up_1yr.status_code == 200

        fc_one = client.get(f"/api/v1/institutions/{inst_id}/forecast", headers=headers)
        assert fc_one.status_code == 200
        fc_one_data = fc_one.json()
        assert fc_one_data["status"] == "INSUFFICIENT_EVIDENCE"
        assert fc_one_data["prediction"] is None
        assert fc_one_data["data_coverage"]["distinct_periods"] == 1

        # 4. Ingest multi-year data (2021-2024) with a clear placement & admission degradation in 2024
        csv_multi = (
            "academic_year,department,sanctioned_intake,enrolled_count,vacancy_count,vacancy_rate,dropouts_year_1,"
            "eligible_students,placed_students,unplaced_count,placement_percentage,median_salary_lpa,max_salary_lpa,"
            "opening_rank,closing_rank,percentile_cutoff\n"
            "2021,CSE,120,118,2,0.0167,1,115,105,10,91.3,6.8,18.0,1200,4500,94.0\n"
            "2022,CSE,120,116,4,0.0333,2,112,100,12,89.29,6.5,17.5,1400,5200,92.5\n"
            "2023,CSE,120,110,10,0.0833,3,108,88,20,81.48,5.8,15.0,1800,7800,88.0\n"
            "2024,CSE,120,72,48,0.4000,14,100,42,58,42.0,4.1,11.0,3500,19500,71.0\n"
        )
        up_multi = client.post(
            "/api/v1/ingest/upload",
            headers=headers,
            files={"file": ("cse_longitudinal_2021_2024.csv", io.BytesIO(csv_multi.encode("utf-8")), "text/csv")},
            data={"institution_id": inst_id},
        )
        assert up_multi.status_code == 200

        # 5. Verify Intelligence Report: Epistemic separation & missing dataset visibility
        intel_res = client.get(f"/api/v1/institutions/{inst_id}/intelligence", headers=headers)
        assert intel_res.status_code == 200
        intel = intel_res.json()
        assert len(intel["evaluated_anomalies"]) >= 1
        for anom in intel["evaluated_anomalies"]:
            assert anom["what_changed"]
            assert anom["by_how_much"]
            assert anom["when"]
            assert anom["comparison_basis"]
            assert anom["significance"]
            assert len(anom["evidence"]) >= 1

        for csf in intel["cross_signal_findings"]:
            assert csf["causal_evidence_present"] is False
            assert csf["correlation_vs_causation_note"]
            assert len(csf["potential_contributing_factors"]) >= 1
            assert len(csf["uninspected_missing_domains"]) >= 1

        assert len(intel["investigations"]) >= 1
        for inv_obj in intel["investigations"]:
            assert inv_obj["finding"]
            assert len(inv_obj["evidence"]) >= 1
            assert len(inv_obj["potential_contributing_factors"]) >= 1
            assert len(inv_obj["alternative_explanations"]) >= 1
            assert 0.0 < inv_obj["confidence"] <= 1.0
            assert len(inv_obj["missing_information"]) >= 1

        # 6. Verify 7-Field Provenance Catalog for every major insight
        prov_res = client.get(f"/api/v1/institutions/{inst_id}/evidence-provenance", headers=headers)
        assert prov_res.status_code == 200
        prov_cat = prov_res.json()
        assert len(prov_cat["insights"]) >= 1
        for insight in prov_cat["insights"]:
            assert len(insight["provenance"]) >= 1
            for rec in insight["provenance"]:
                for field_name in (
                    "source",
                    "document",
                    "page_or_section",
                    "table_cell_or_range",
                    "excerpt_or_image",
                    "extraction_confidence",
                    "date_or_context",
                ):
                    assert rec.get(field_name) not in (None, ""), f"Missing {field_name} in provenance record"

        # 7. Verify Explainable Prediction exposes method, inputs, evidence, confidence, horizon, limitations
        fc_multi = client.get(f"/api/v1/institutions/{inst_id}/forecast", headers=headers)
        assert fc_multi.status_code == 200
        fc = fc_multi.json()
        assert fc["status"] == "SUFFICIENT_EVIDENCE"
        assert fc["method_actually_used"]
        assert len(fc["input_signals"]) >= 1
        assert len(fc["historical_evidence"]) >= 1
        assert 0.0 < fc["confidence"] <= 1.0
        assert fc["horizon"]
        assert len(fc["limitations"]) >= 1
        assert len(fc["prediction"]) == 3

        # 8. Verify Natural Question Investigation & AI Executive Analysis honesty
        inv_res = client.post(
            f"/api/v1/institutions/{inst_id}/investigate-question",
            headers=headers,
            json={"question": "Why did placements fall?"},
        )
        assert inv_res.status_code == 200
        inv = inv_res.json()
        assert inv["finding"]
        assert len(inv["evidence"]) >= 1
        assert len(inv["related_signals"]) >= 1
        assert len(inv["potential_contributing_factors"]) >= 1
        assert len(inv["alternative_explanations"]) >= 1
        assert 0.0 < inv["confidence"] <= 1.0
        assert len(inv["missing_information"]) >= 1
        assert inv["generation_mode"] in ("LIVE_GEMINI", "LIVE_OPENROUTER", "LIVE_GROQ", "DETERMINISTIC_FALLBACK")


def test_final_gate_auth_and_security_isolation():
    """
    Verify Auth & Security:
    - Unauthenticated APIs return 401
    - Unauthorized role/scope actions return 403
    - User isolation, Organization isolation, Institution isolation, Department/Program isolation
    - File upload size (>15 MB -> 413) & unsupported format (-> 415 UNSUPPORTED_FORMAT)
    - Session revocation on logout (-> 401 after logout)
    """
    tag = uuid.uuid4().hex[:6].upper()
    org_a = f"ORG_SEC_A_{tag}"
    inst_a1 = f"INST_SEC_A1_{tag}"
    inst_a2 = f"INST_SEC_A2_{tag}"
    inst_b1 = f"INST_SEC_B1_{tag}"
    email_a = f"sec_user_a_{tag.lower()}@orga.edu"
    email_b = f"sec_user_b_{tag.lower()}@orgb.edu"
    email_c = f"dept_coord_{tag.lower()}@orga.edu"

    with TestClient(app) as client:
        # 1. Unauthenticated API checks (401)
        for endpoint in (
            "/api/v1/institutions",
            "/api/v1/profiles/user",
            "/api/v1/profiles/organization",
            f"/api/v1/institutions/{inst_a1}/evaluate",
            f"/api/v1/organizations/{org_a}/intelligence",
        ):
            r = client.get(endpoint)
            assert r.status_code == 401, f"Expected 401 for unauthenticated GET {endpoint}, got {r.status_code}"

        # 2. Create User A (Org A -> inst_a1 with CSE & MECH) and User B (Org B -> inst_b1)
        sa = client.post(
            "/api/v1/auth/signup",
            json={"email": email_a, "password": "PasswordA123!", "full_name": "User A"},
        )
        assert sa.status_code in (200, 201)
        tok_a = sa.json()["access_token"]
        hdr_a = {"Authorization": f"Bearer {tok_a}"}

        client.post(
            "/api/v1/auth/onboarding",
            headers=hdr_a,
            json={
                "entity_id": org_a,
                "entity_name": "Security Group A",
                "entity_category": "educational_group_network",
                "ownership_governance": "Trust",
                "education_entity_type": "College Group",
                "academic_domains": ["Engineering & Technology"],
                "state": "Karnataka",
            },
        )
        client.post(
            "/api/v1/profiles/institutions",
            headers=hdr_a,
            json={
                "institution_id": inst_a1,
                "name": "Security Engineering College A1",
                "education_entity_type": "College",
                "ownership_governance": "Trust",
                "academic_domain": "Engineering & Technology",
                "parent_organization_id": org_a,
            },
        )
        client.post(
            "/api/v1/profiles/institutions",
            headers=hdr_a,
            json={
                "institution_id": inst_a2,
                "name": "Security Medical College A2",
                "education_entity_type": "College",
                "ownership_governance": "Trust",
                "academic_domain": "Medicine",
                "parent_organization_id": org_a,
            },
        )

        # Ingest CSE & MECH data into inst_a1
        csv_a1 = (
            "academic_year,department,sanctioned_intake,enrolled_count,vacancy_count,vacancy_rate,dropouts_year_1\n"
            "2023,CSE,120,115,5,0.0417,1\n"
            "2024,CSE,120,100,20,0.1667,3\n"
            "2023,MECH,60,55,5,0.0833,1\n"
            "2024,MECH,60,40,20,0.3333,4\n"
        )
        client.post(
            "/api/v1/ingest/upload",
            headers=hdr_a,
            files={"file": ("a1_depts.csv", io.BytesIO(csv_a1.encode("utf-8")), "text/csv")},
            data={"institution_id": inst_a1},
        )

        # Create User B (separate organization/user)
        sb = client.post(
            "/api/v1/auth/signup",
            json={"email": email_b, "password": "PasswordB123!", "full_name": "User B"},
        )
        assert sb.status_code in (200, 201)
        tok_b = sb.json()["access_token"]
        hdr_b = {"Authorization": f"Bearer {tok_b}"}
        client.post(
            "/api/v1/auth/onboarding",
            headers=hdr_b,
            json={
                "entity_id": inst_b1,
                "entity_name": "Isolated College B1",
                "entity_category": "educational_institution",
                "ownership_governance": "Private",
                "education_entity_type": "College",
                "academic_domains": ["Science"],
                "state": "Karnataka",
            },
        )

        # 3. Cross-user & Cross-organization isolation (403)
        assert client.get(f"/api/v1/institutions/{inst_a1}/evaluate", headers=hdr_b).status_code == 403
        assert client.get(f"/api/v1/organizations/{org_a}/intelligence", headers=hdr_b).status_code == 403
        assert client.get(f"/api/v1/ingest/institutions/{inst_a1}/signals", headers=hdr_b).status_code == 403

        # 4. Institution & Department/Program isolation within Org A
        sc = client.post(
            "/api/v1/auth/signup",
            json={"email": email_c, "password": "PasswordC123!", "full_name": "CSE Coordinator"},
        )
        tok_c = sc.json()["access_token"]
        hdr_c = {"Authorization": f"Bearer {tok_c}"}

        # User A grants User C department_program scope to ONLY inst_a1 -> CSE
        perm_res = client.post(
            "/api/v1/profiles/permissions",
            headers=hdr_a,
            json={
                "target_email": email_c,
                "access_scope_level": "department_program",
                "allowed_organization_ids": [org_a],
                "allowed_institution_ids": [inst_a1],
                "allowed_departments_by_institution": {inst_a1: ["CSE"]},
            },
        )
        assert perm_res.status_code == 200

        # User C can evaluate inst_a1 (filtered to CSE), cannot access inst_a2 (403), cannot query MECH (403)
        assert client.get(f"/api/v1/institutions/{inst_a1}/evaluate?department=CSE", headers=hdr_c).status_code == 200
        assert client.get(f"/api/v1/institutions/{inst_a1}/evaluate?department=MECH", headers=hdr_c).status_code == 403
        assert client.get(f"/api/v1/institutions/{inst_a2}/evaluate", headers=hdr_c).status_code == 403

        # 5. File upload validation: unsupported format (415) & oversized file (413)
        bad_fmt = client.post(
            "/api/v1/ingest/upload",
            headers=hdr_a,
            files={"file": ("legacy_archive.xyz", io.BytesIO(b"binary-content"), "application/octet-stream")},
            data={"institution_id": inst_a1},
        )
        assert bad_fmt.status_code == 415
        assert bad_fmt.json()["status"] == "UNSUPPORTED_FORMAT"

        oversized_bytes = b"a" * (15 * 1024 * 1024 + 1024)
        big_res = client.post(
            "/api/v1/ingest/upload",
            headers=hdr_a,
            files={"file": ("huge.csv", io.BytesIO(oversized_bytes), "text/csv")},
            data={"institution_id": inst_a1},
        )
        assert big_res.status_code == 413

        # 6. Session revocation on Logout
        logout_res = client.post("/api/v1/auth/logout", headers=hdr_b)
        assert logout_res.status_code == 200
        after_logout = client.get("/api/v1/profiles/user", headers=hdr_b)
        assert after_logout.status_code == 401


def test_final_gate_real_browser_21_step_user_journey():
    """
    Launch a live FastAPI uvicorn server and run a full 21-step real-browser user journey
    in Google Chrome via Playwright:
    1. Signup
    2. Login
    3. Entity onboarding/classification
    4. Profile
    5. Organization/institution setup
    6. Add Data
    7. Processing
    8. Signal discovery
    9. Overview
    10. Insights
    11. Risks
    12. Investigation
    13. Evidence
    14. Prediction
    15. Why prediction changed
    16. What-If Analysis
    17. Institutional Memory
    18. What Changed
    19. Report export
    20. Logout
    21. Login again
    """
    from playwright.sync_api import sync_playwright

    chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    if not os.path.exists(chrome_path):
        chrome_path = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    assert os.path.exists(chrome_path), "Real Chrome/Edge binary is required for Final Demo Gate"

    port = _find_free_port()
    base_url = f"http://127.0.0.1:{port}"

    tmp_dir = tempfile.mkdtemp(prefix="cip_final_gate_")
    db_file = Path(tmp_dir) / "cip_final_gate.db"
    server_log_path = Path(tmp_dir) / "uvicorn.log"
    server_log_file = open(server_log_path, "w", encoding="utf-8")
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_file.as_posix()}"

    server_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "src.api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=server_log_file,
        stderr=subprocess.STDOUT,
    )

    try:
        # Wait for uvicorn readiness
        import httpx

        server_ready = False
        for _ in range(40):
            try:
                r = httpx.get(f"{base_url}/health", timeout=1.0)
                if r.status_code == 200:
                    server_ready = True
                    break
            except Exception:
                pass
            time.sleep(0.25)
        assert server_ready, "Live uvicorn server failed to start within timeout"

        # Prepare two realistic CSV files for longitudinal ingestion (2021-2023 baseline + 2024 degradation)
        csv_batch_1_path = Path(tmp_dir) / "horizon_engg_2021_2023.csv"
        csv_batch_1_path.write_text(
            "academic_year,department,sanctioned_intake,enrolled_count,vacancy_count,vacancy_rate,dropouts_year_1,"
            "eligible_students,placed_students,unplaced_count,placement_percentage,median_salary_lpa,max_salary_lpa,"
            "opening_rank,closing_rank,percentile_cutoff\n"
            "2021,CSE,120,118,2,0.0167,1,115,106,9,92.17,6.8,18.0,1100,4200,94.5\n"
            "2022,CSE,120,115,5,0.0417,2,112,98,14,87.50,6.4,16.5,1400,5400,92.0\n"
            "2023,CSE,120,108,12,0.1000,4,106,84,22,79.25,5.7,14.5,1900,8200,87.5\n",
            encoding="utf-8",
        )

        csv_batch_2_path = Path(tmp_dir) / "horizon_engg_2024_update.csv"
        csv_batch_2_path.write_text(
            "academic_year,department,sanctioned_intake,enrolled_count,vacancy_count,vacancy_rate,dropouts_year_1,"
            "eligible_students,placed_students,unplaced_count,placement_percentage,median_salary_lpa,max_salary_lpa,"
            "opening_rank,closing_rank,percentile_cutoff\n"
            "2024,CSE,120,68,52,0.4333,16,100,39,61,39.00,4.0,10.5,3800,21400,68.5\n",
            encoding="utf-8",
        )

        page_errors = []
        console_errors = []
        cors_or_network_failures = []
        server_5xx_responses = []

        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=chrome_path, headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 920}, accept_downloads=True)
            page = context.new_page()

            page.on("pageerror", lambda exc: page_errors.append(str(exc)))

            def _on_console(msg):
                if msg.type == "error":
                    text = msg.text
                    # Ignore Tailwind CDN advisory notices if any
                    if "cdn.tailwindcss.com" not in text:
                        console_errors.append(text)

            page.on("console", _on_console)

            def _on_request_failed(req):
                if req.url.startswith(base_url):
                    cors_or_network_failures.append(f"{req.method} {req.url} -> {req.failure}")

            page.on("requestfailed", _on_request_failed)

            def _on_response(res):
                if res.url.startswith(base_url) and res.status >= 500:
                    server_5xx_responses.append(f"{res.status} {res.url}")

            page.on("response", _on_response)

            # Open CIP Web App
            page.goto(f"{base_url}/dashboard/", wait_until="networkidle")
            assert "CIP — Crisis Intelligence Platform" in page.title()

            # =================================================================
            # STEP 1: SIGNUP
            # =================================================================
            page.click("#tabSignUpBtn")
            page.fill("#signupFullName", "Dr. Arundhati Rao")
            page.fill("#signupJobTitle", "Vice Chancellor & Director")
            page.fill("#signupDeptUnit", "Executive Council")
            page.fill("#signupEmail", "arundhati.rao@horizon-edu.org")
            page.fill("#signupPassword", "HorizonGate2026!")
            page.click("#signupSubmitBtn")

            # Wait for Onboarding Modal to open after signup
            page.wait_for_selector("#onboardingModal:not(.hidden)", timeout=10000)

            # =================================================================
            # STEP 2: LOGIN (Explicit verification of Sign Out -> Sign In before onboarding)
            # =================================================================
            page.evaluate("handleLogout()")
            page.wait_for_selector("#loginModal:not(.hidden)", timeout=5000)
            page.click("#tabSignInBtn")
            page.fill("#loginEmail", "arundhati.rao@horizon-edu.org")
            page.fill("#loginPassword", "HorizonGate2026!")
            page.click("#loginSubmitBtn")

            # Because onboarding is not yet completed, login opens the Onboarding Modal
            page.wait_for_selector("#onboardingModal:not(.hidden)", timeout=10000)

            # =================================================================
            # STEP 3: ENTITY ONBOARDING / CLASSIFICATION
            # =================================================================
            page.fill("#onboardEntityId", "INST_GATE_ENGG")
            page.fill("#onboardEntityName", "Horizon Institute of Engineering & Technology")
            page.select_option("#onboardEntityType", "educational_institution")
            page.select_option("#onboardOwnership", "Trust")
            page.select_option("#onboardEduLevel", "College")
            page.fill("#onboardState", "Karnataka")
            page.fill("#onboardCity", "Bengaluru")
            page.fill("#onboardParentOrg", "ORG_HORIZON_NET")
            page.click("#onboardSubmitBtn")

            page.wait_for_selector("#onboardingModal", state="hidden", timeout=10000)
            page.wait_for_function(
                "() => document.getElementById('liveBannerText').innerText.includes(\"Workspace 'INST_GATE_ENGG' has no ingested signal records yet\")",
                timeout=15000,
            )
            assert page.inner_text("#roleBadge").strip() == "arundhati.rao@horizon-edu.org"

            # =================================================================
            # STEP 4: PROFILE
            # =================================================================
            page.click("#nav-profile")
            page.wait_for_selector("#view-profile:not(.hidden)", timeout=5000)
            page.click("#ptabUser")
            page.wait_for_selector("#pviewUser:not(.hidden)", timeout=5000)
            assert page.input_value("#profUserEmail") == "arundhati.rao@horizon-edu.org"
            assert "Arundhati Rao" in page.input_value("#profUserFullName")

            # =================================================================
            # STEP 5: ORGANIZATION / INSTITUTION SETUP
            # =================================================================
            # 5a. Update Organization Profile
            page.click("#ptabOrganization")
            page.wait_for_selector("#pviewOrganization:not(.hidden)", timeout=5000)
            page.fill("#profOrgName", "Horizon Educational Trust & Network")
            page.fill("#profOrgCategory", "educational_group_network")
            page.fill("#profOrgOwnership", "Trust")
            page.fill("#profOrgEduType", "College Group")
            page.click("#orgProfileForm button[type='submit']")
            page.wait_for_function(
                "() => !document.getElementById('profileActionNotice').classList.contains('hidden')"
            )

            # 5b. Add Department node (CSE) in Entity Hierarchy
            page.click("#ptabStructure")
            page.wait_for_selector("#pviewStructure:not(.hidden)", timeout=5000)
            page.select_option("#childNodeType", "department")
            page.fill("#childNodeCode", "CSE")
            page.fill("#childNodeName", "Department of Computer Science & Engineering")
            page.fill("#childNodeLevel", "UG")
            page.fill("#childNodeIntake", "120")
            page.click("#addChildNodeForm button[type='submit']")
            page.wait_for_function(
                "() => document.getElementById('hierarchyTreeContainer').innerText.includes('CSE')"
            )

            # 5c. Add a second constituent institution (PUC) to the Organization Network
            page.click("#ptabInstitution")
            page.wait_for_selector("#pviewInstitution:not(.hidden)", timeout=5000)
            page.fill("#newInstId", "INST_GATE_PUC")
            page.fill("#newInstName", "Horizon Pre-University Science College")
            page.select_option("#newInstEduType", "PUC/Pre-University")
            page.fill("#newInstOwnership", "Trust")
            page.fill("#newInstDomain", "Science")
            page.click("#createNewInstForm button[type='submit']")
            page.wait_for_function(
                "() => document.getElementById('profInstId').value === 'INST_GATE_PUC'",
                timeout=10000,
            )

            # Switch active institution back to INST_GATE_ENGG for longitudinal ingestion
            page.select_option("#activeInstitutionSelect", "INST_GATE_ENGG")
            page.wait_for_function(
                "() => document.getElementById('liveBannerText').innerText.includes(\"Workspace 'INST_GATE_ENGG' has no ingested signal records yet\")",
                timeout=15000,
            )

            # =================================================================
            # STEP 6: ADD DATA (Batch 1: 2021-2023 + Batch 2: 2024 update)
            # =================================================================
            page.click("#nav-data")
            page.wait_for_selector("#view-data:not(.hidden)", timeout=5000)

            page.set_input_files("#universalUploadInput", str(csv_batch_1_path))
            page.wait_for_function(
                "() => document.getElementById('ingestionStatusBox').innerText.includes('horizon_engg_2021_2023.csv') && document.getElementById('universalUploadInput').value === ''",
                timeout=30000,
            )

            # Upload 2024 update to trigger longitudinal delta & forecast change explanation
            page.set_input_files("#universalUploadInput", str(csv_batch_2_path))
            page.wait_for_function(
                "() => document.getElementById('ingestionStatusBox').innerText.includes('horizon_engg_2024_update.csv') && document.getElementById('universalUploadInput').value === ''",
                timeout=30000,
            )

            # =================================================================
            # STEP 7: PROCESSING
            # =================================================================
            ing_status_text = page.inner_text("#ingestionStatusBox")
            assert "Detected Format: CSV" in ing_status_text
            assert "Processing Status: SUCCESS" in ing_status_text
            assert "Extraction Confidence:" in ing_status_text

            # =================================================================
            # STEP 8: SIGNAL DISCOVERY
            # =================================================================
            sig_text = page.inner_text("#discoveredSignalsList")
            assert "CSE" in sig_text
            assert "2024" in sig_text
            assert "horizon_engg_2024_update.csv" in sig_text
            dom_badge_text = page.inner_text("#discoveredDomainsBadge")
            assert "Signals" in dom_badge_text and not dom_badge_text.startswith("0 Domains (0")

            # =================================================================
            # STEP 9: OVERVIEW (Institution View + Organization / Network View)
            # =================================================================
            page.click("#nav-overview")
            page.wait_for_selector("#view-overview:not(.hidden)", timeout=5000)
            page.wait_for_function(
                "() => document.getElementById('criValue').innerText !== '—' && document.getElementById('criValue').innerText !== '0.000'",
                timeout=10000,
            )
            cri_val = float(page.inner_text("#criValue").strip())
            assert 0.35 <= cri_val <= 1.0
            assert "What is happening?" in page.inner_text("#execSummaryBox")
            assert "Supporting Evidence (7-Field Provenance):" in page.inner_text("#execSummaryBox")

            # Also verify Organization / Network View in Overview
            page.click("#viewModeOrganizationBtn")
            page.wait_for_selector("#organizationNetworkViewPanel:not(.hidden)", timeout=5000)
            page.wait_for_function(
                "() => document.getElementById('orgInstCountVal').innerText.includes('2 Institutions')",
                timeout=10000,
            )
            org_table_text = page.inner_text("#orgInstitutionRisksTableBody")
            assert "INST_GATE_ENGG" in org_table_text
            assert "INST_GATE_PUC" in org_table_text

            # Switch back to Institution View
            page.click("#viewModeInstitutionBtn")
            page.wait_for_selector("#institutionOverviewContent:not(.hidden)", timeout=5000)
            page.wait_for_function(
                "() => document.getElementById('liveBannerText').innerText.includes('Evaluated INST_GATE_ENGG')",
                timeout=30000,
            )

            if ARTIFACT_DIR.exists():
                page.screenshot(path=str(ARTIFACT_DIR / "cip_final_gate_overview.png"))

            # =================================================================
            # STEP 10: INSIGHTS
            # =================================================================
            page.click("#nav-insights")
            page.wait_for_selector("#view-insights:not(.hidden)", timeout=5000)
            insights_text = page.inner_text("#insightsDetailedList")
            assert "1. Change:" in insights_text
            assert "2. Magnitude:" in insights_text
            assert "3. Period & Basis:" in insights_text
            assert "4. Significance:" in insights_text
            assert "5. Supporting Evidence:" in insights_text
            assert "6. Next Investigation Area:" in insights_text

            # =================================================================
            # STEP 11: RISKS
            # =================================================================
            page.click("#nav-risks")
            page.wait_for_selector("#view-risks:not(.hidden)", timeout=5000)
            risk_prog_text = page.inner_text("#riskProgressionSummary")
            assert "Observation:" in risk_prog_text
            assert "Anomaly:" in risk_prog_text
            assert "1. Change:" in page.inner_text("#riskDetailedFindingsBox")

            # =================================================================
            # STEP 12: INVESTIGATION
            # =================================================================
            page.click("#nav-investigations")
            page.wait_for_selector("#view-investigations:not(.hidden)", timeout=5000)
            page.fill("#naturalQuestionInput", "Why did placements fall?")
            page.click("#view-investigations button:has-text('Investigate')")
            page.wait_for_function(
                "() => document.getElementById('naturalInvestigationResultBox').innerText.includes('Potential Contributing Factors')",
                timeout=25000,
            )
            inv_box_text = page.inner_text("#naturalInvestigationResultBox")
            assert "Question: Why did placements fall?" in inv_box_text
            assert "Finding:" in inv_box_text
            assert "Potential Contributing Factors (Non-Causal):" in inv_box_text
            assert "Alternative Explanations:" in inv_box_text
            assert "Missing Information:" in inv_box_text
            assert "Supporting 7-Field Evidence" in inv_box_text

            # =================================================================
            # STEP 13: EVIDENCE
            # =================================================================
            page.click("#nav-evidence")
            page.wait_for_selector("#view-evidence:not(.hidden)", timeout=5000)
            ev_body_text = page.inner_text("#selectedInsightProvenanceBody")
            for prov_label in (
                "1. Source:",
                "2. Document:",
                "3. Page/Section:",
                "4. Table/Cell/Range:",
                "5. Excerpt/Image:",
                "6. Extraction Confidence:",
                "7. Date/Context:",
            ):
                assert prov_label in ev_body_text, f"Missing '{prov_label}' in Evidence inspector"

            # =================================================================
            # STEP 14: PREDICTION & STEP 15: WHY PREDICTION CHANGED
            # =================================================================
            page.click("#nav-predictions")
            page.wait_for_selector("#view-predictions:not(.hidden)", timeout=5000)
            fc_box_text = page.inner_text("#explainableForecastBox")
            assert "1. Horizon:" in fc_box_text
            assert "2. Confidence & Coverage:" in fc_box_text
            assert "3. Method Actually Used:" in fc_box_text
            assert "4. Forecast Trajectory Points:" in fc_box_text
            assert '5. Reason for Change ("Why did the prediction change?"):' in fc_box_text
            assert "6. Input Signals & Trajectory Drivers:" in fc_box_text
            assert "7. Historical Evidence & 8. Limitations:" in fc_box_text

            # =================================================================
            # STEP 16: WHAT-IF ANALYSIS
            # =================================================================
            page.click("#nav-whatif")
            page.wait_for_selector("#view-whatif:not(.hidden)", timeout=5000)
            page.evaluate(
                "() => { document.getElementById('plcSlider').value = '12'; document.getElementById('vacSlider').value = '15'; updateSimulation(); }"
            )
            page.wait_for_function(
                "() => document.getElementById('plcBoostVal').innerText === '+12.0%' && document.getElementById('sqYr3Val').innerText !== '---' && document.getElementById('sqYr3Val').innerText !== '—'",
                timeout=10000,
            )
            sq_yr3 = float(page.inner_text("#sqYr3Val").strip())
            iv_yr3 = float(page.inner_text("#ivYr3Val").strip())
            assert iv_yr3 <= sq_yr3
            assert "Year +3" in page.inner_text("#whatIfTrajectoryTableBody")

            # =================================================================
            # STEP 17: INSTITUTIONAL MEMORY
            # =================================================================
            page.click("#nav-memory")
            page.wait_for_selector("#view-memory:not(.hidden)", timeout=5000)
            mem_text = page.inner_text("#institutionalMemoryBox")
            for cat in ("observed_fact:", "analysis:", "inference:", "prediction:", "outcome:", "user_feedback:", "unknown:"):
                assert cat in mem_text
            # Submit reviewer feedback in Institutional Memory
            confirm_btns = page.locator("#institutionalMemoryBox button:has-text('Confirmed')")
            if confirm_btns.count() > 0:
                confirm_btns.first.click()
                page.wait_for_function(
                    "() => document.getElementById('liveBannerText').innerText.includes('user_feedback')",
                    timeout=10000,
                )

            # =================================================================
            # STEP 18: WHAT CHANGED
            # =================================================================
            page.click("#nav-risks")
            page.wait_for_selector("#view-risks:not(.hidden)", timeout=5000)
            ew_wc_text = page.inner_text("#earlyWarningsAndWhatChangedList")
            assert "[WHAT CHANGED]" in ew_wc_text

            # Also verify natural question "What changed since the last analysis?"
            page.click("#nav-investigations")
            page.wait_for_selector("#view-investigations:not(.hidden)", timeout=5000)
            page.click("#view-investigations button:has-text('What changed since the last analysis?')")
            page.wait_for_function(
                "() => document.getElementById('naturalInvestigationResultBox').innerText.includes('What changed since the last analysis?')",
                timeout=25000,
            )

            # =================================================================
            # STEP 19: REPORT EXPORT
            # =================================================================
            with page.expect_download(timeout=30000) as download_info:
                page.click("#exportPdfBtn")
            download = download_info.value
            assert download.suggested_filename == "cip_report_INST_GATE_ENGG.pdf"
            dl_path = Path(tmp_dir) / download.suggested_filename
            download.save_as(str(dl_path))
            pdf_bytes = dl_path.read_bytes()
            assert pdf_bytes.startswith(b"%PDF-1.4")
            assert len(pdf_bytes) > 1000

            # =================================================================
            # STEP 20: LOGOUT
            # =================================================================
            page.click("#logoutBtn")
            page.wait_for_selector("#loginModal:not(.hidden)", timeout=5000)
            assert page.inner_text("#roleBadge").strip() == "Unauthenticated"
            jwt_after_logout = page.evaluate("() => localStorage.getItem('aicriss_jwt')")
            assert jwt_after_logout is None

            # =================================================================
            # STEP 21: LOGIN AGAIN
            # =================================================================
            page.click("#tabSignInBtn")
            page.fill("#loginEmail", "arundhati.rao@horizon-edu.org")
            page.fill("#loginPassword", "HorizonGate2026!")
            page.click("#loginSubmitBtn")
            page.wait_for_selector("#loginModal", state="hidden", timeout=15000)
            page.wait_for_function(
                "() => document.getElementById('criValue').innerText !== '—' && document.getElementById('criValue').innerText !== '0.000'",
                timeout=30000,
            )
            assert page.inner_text("#roleBadge").strip() == "arundhati.rao@horizon-edu.org"
            assert abs(float(page.inner_text("#criValue").strip()) - cri_val) < 1e-3

            browser.close()

        # Assert Browser Health
        assert page_errors == [], f"Critical JS pageerrors detected: {page_errors}"
        assert console_errors == [], f"Browser console.errors detected: {console_errors}"
        assert cors_or_network_failures == [], f"CORS/Network failures detected: {cors_or_network_failures}"
        assert server_5xx_responses == [], f"Server 5xx errors detected: {server_5xx_responses}"

    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except Exception:
            server_proc.kill()
        try:
            server_log_file.close()
        except Exception:
            pass
