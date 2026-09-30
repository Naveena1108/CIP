"""
CIP Phase 5 Verification Suite: Evidence-Grounded AI, Deep 7-Field Provenance,
Natural-Language Investigation, Truthful Gemini vs Deterministic Fallback,
and Hallucination Resistance.

Covers all required verification targets:
1. provenance (7-field ProvenanceRecord + clickable insight catalog)
2. investigation ("Why did retention decline?", "Why did placements fall?", "What changed since the last analysis?")
3. contradictions (conflicting sources surfaced in provenance, investigation, and uncertain claims)
4. missing evidence (explicitly reported when querying unmonitored domains like retention)
5. Gemini available/unavailable (LIVE_GEMINI vs DETERMINISTIC_FALLBACK truthful distinction)
6. fallback (graceful fallback on missing credentials, network failure, or malformed JSON)
7. hallucination resistance (deterministic lock on CRI/risk_level/forecasts + flagging unverified numbers)
"""

import json
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from src.api.main import app
from src.contracts import (
    CrisisAssessment,
    SignalAnomaly,
)
from src.engine.evidence import EvidenceAssembler
from src.engine.llm_reasoner import LLMStructuredReasoner
from src.engine.investigation_engine import EvidenceGroundedInvestigationEngine
from src.engine.institutional_intelligence import InstitutionalIntelligenceEngine


def _get_auth_headers(client: TestClient, email: str = "phase5_lead@cip.org") -> dict:
    signup_res = client.post(
        "/api/v1/auth/signup",
        json={
            "email": email,
            "password": "Phase5SecurePassword!123",
            "full_name": "Phase 5 Lead Engineer",
        },
    )
    if signup_res.status_code in (200, 201):
        token = signup_res.json()["access_token"]
    else:
        login_res = client.post(
            "/api/v1/auth/login",
            data={"username": email, "password": "Phase5SecurePassword!123"},
        )
        assert login_res.status_code == 200, login_res.text
        token = login_res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_phase5_provenance_7_fields_and_clickable_insights():
    """
    Verify that every major finding links to available 7-field provenance:
    source, document, page_or_section, table_cell_or_range, excerpt_or_image,
    extraction_confidence, and date_or_context, and that the dashboard exposes
    AI Executive Analysis and clickable insight provenance drill-down.
    """
    with TestClient(app) as client:
        headers = _get_auth_headers(client)

        # Seed a multi-year cascading crisis institution
        inst_id = "INST_P5_PROV_01"
        ing_res = client.post(
            "/api/v1/ingest/synthetic",
            json={
                "institution_id": inst_id,
                "scenario": "CASCADING_CRISIS",
                "start_year": 2021,
                "num_years": 4,
            },
            headers=headers,
        )
        assert ing_res.status_code in (200, 201), ing_res.text

        # Check /evidence-provenance catalog
        prov_res = client.get(f"/api/v1/institutions/{inst_id}/evidence-provenance", headers=headers)
        assert prov_res.status_code == 200, prov_res.text
        catalog = prov_res.json()

        assert catalog["institution_id"] == inst_id
        assert catalog["total_insights"] > 0
        assert catalog["total_provenance_records"] > 0
        assert len(catalog["insights"]) > 0

        # Every insight must link to provenance records containing all 7 required fields
        required_7_fields = {
            "source",
            "document",
            "page_or_section",
            "table_cell_or_range",
            "excerpt_or_image",
            "extraction_confidence",
            "date_or_context",
        }
        for insight in catalog["insights"]:
            assert len(insight["provenance"]) > 0, f"Insight {insight['insight_id']} missing provenance links"
            for rec in insight["provenance"]:
                for field_name in required_7_fields:
                    assert field_name in rec, f"Missing required provenance field '{field_name}'"
                    assert rec[field_name] is not None and str(rec[field_name]).strip() != ""
                assert 0.0 <= float(rec["extraction_confidence"]) <= 1.0

        # Verify UI replaces "Grounded Executive Briefing" with "AI Executive Analysis" and wires clickable insights
        dash_res = client.get("/dashboard/")
        assert dash_res.status_code == 200
        html = dash_res.text
        assert "AI Executive Analysis" in html
        assert "Grounded Executive Briefing" not in html
        assert "revealInsightEvidence" in html
        assert "askNaturalInvestigationQuestion" in html


def test_phase5_natural_questions_and_missing_evidence():
    """
    Verify natural-language investigation for:
    - "Why did placements fall?" (backed by placement anomaly evidence)
    - "Why did retention decline?" (explicitly reports missing direct retention evidence)
    - "What changed since the last analysis?" (reports longitudinal signal and risk deltas)
    """
    with TestClient(app) as client:
        headers = _get_auth_headers(client)
        inst_id = "INST_P5_INV_01"

        ing_res = client.post(
            "/api/v1/ingest/synthetic",
            json={
                "institution_id": inst_id,
                "scenario": "CASCADING_CRISIS",
                "start_year": 2021,
                "num_years": 4,
            },
            headers=headers,
        )
        assert ing_res.status_code in (200, 201), ing_res.text
        # Run an initial evaluation so a persisted assessment snapshot exists
        client.get(f"/api/v1/institutions/{inst_id}/evaluate", headers=headers)

        # 1. "Why did placements fall?"
        q1_res = client.post(
            f"/api/v1/institutions/{inst_id}/investigate-question",
            json={"question": "Why did placements fall?"},
            headers=headers,
        )
        assert q1_res.status_code == 200, q1_res.text
        q1 = q1_res.json()
        assert q1["detected_intent"] == "placement_decline"
        assert "placement" in q1["finding"].lower()
        assert len(q1["evidence"]) > 0
        assert len(q1["potential_contributing_factors"]) > 0
        assert len(q1["alternative_explanations"]) > 0
        assert q1["confidence"] >= 0.70

        # 2. "Why did retention decline?" -> Must explicitly report missing direct retention evidence
        q2_res = client.post(
            f"/api/v1/institutions/{inst_id}/investigate-question",
            json={"question": "Why did retention decline?"},
            headers=headers,
        )
        assert q2_res.status_code == 200, q2_res.text
        q2 = q2_res.json()
        assert q2["detected_intent"] == "retention_decline"
        assert "insufficient" in q2["finding"].lower() or "not been ingested" in q2["finding"].lower()
        assert any("retention" in m.lower() for m in q2["missing_information"])
        assert q2["confidence"] <= 0.40

        # 3. "What changed since the last analysis?"
        q3_res = client.post(
            f"/api/v1/institutions/{inst_id}/investigate-question",
            json={"question": "What changed since the last analysis?"},
            headers=headers,
        )
        assert q3_res.status_code == 200, q3_res.text
        q3 = q3_res.json()
        assert q3["detected_intent"] == "what_changed"
        assert len(q3["finding"]) > 15
        assert len(q3["evidence"]) > 0


def test_phase5_contradictions_surfaced_in_investigation_and_ai_analysis():
    """
    Verify that contradictory observations uploaded from conflicting sources are preserved
    and explicitly surfaced in provenance, AI Executive Analysis uncertain_claims, and
    natural-language investigation contradictions_noted.
    """
    with TestClient(app) as client:
        headers = _get_auth_headers(client)
        inst_id = "INST_P5_CONTRA_01"

        # Seed baseline canonical data first
        client.post(
            "/api/v1/ingest/synthetic",
            json={
                "institution_id": inst_id,
                "scenario": "STABLE_GROWTH",
                "start_year": 2022,
                "num_years": 3,
            },
            headers=headers,
        )

        # Upload CSV with conflicting retention values for the same department & academic year
        csv_content = (
            "institution_id,department,academic_year,retention_rate,source_note\n"
            f"{inst_id},CSE,2024,92.5%,Annual_Academic_Audit_Report\n"
            f"{inst_id},CSE,2024,68.0%,Semester_Continuation_Ledger\n"
        ).encode("utf-8")

        up_res = client.post(
            "/api/v1/ingest/upload",
            data={"institution_id": inst_id},
            files={"file": ("conflicting_retention_2024.csv", csv_content, "text/csv")},
            headers=headers,
        )
        assert up_res.status_code == 200, up_res.text
        up_data = up_res.json()
        assert up_data["contradictions_detected"] >= 1

        # Ask "Why did retention decline?" now that retention records (with contradiction) exist
        inv_res = client.post(
            f"/api/v1/institutions/{inst_id}/investigate-question",
            json={"question": "Why did retention decline?"},
            headers=headers,
        )
        assert inv_res.status_code == 200, inv_res.text
        inv = inv_res.json()
        assert len(inv["contradictions_noted"]) >= 1
        assert any(e["is_contradictory"] for e in inv["evidence"])

        # Check AI Executive Analysis also surfaces the contradiction in uncertain_claims
        ai_res = client.get(f"/api/v1/institutions/{inst_id}/ai-executive-analysis", headers=headers)
        assert ai_res.status_code == 200, ai_res.text
        ai_data = ai_res.json()
        assert ai_data["analysis_title"] == "AI Executive Analysis"
        assert any("CONTRADICTORY" in u.upper() for u in ai_data["uncertain_claims"])


def test_phase5_gemini_available_vs_unavailable_and_fallback():
    """
    Verify truthful distinction between LIVE_GEMINI and DETERMINISTIC_FALLBACK:
    1. When GEMINI_API_KEY is absent -> DETERMINISTIC_FALLBACK, gemini_live_used=False
    2. When GEMINI_API_KEY is present and Gemini succeeds -> LIVE_GEMINI, gemini_live_used=True
    3. When GEMINI_API_KEY is present but Gemini errors -> DETERMINISTIC_FALLBACK, gemini_live_used=False
    """
    assessment = CrisisAssessment(
        institution_id="INST_P5_MODE",
        composite_risk_index=0.68,
        risk_level="HIGH",
        primary_driving_signal="Placement Decline",
        confidence_score=0.91,
        anomalies_detected=[
            SignalAnomaly(
                signal_name="Placements (CSE)",
                academic_year=2024,
                metric_name="placement_percentage",
                observed_value=42.0,
                baseline_value=82.0,
                deviation_zscore=-2.95,
                severity="CRITICAL",
                description="Placement percentage dropped from 82.0% to 42.0%.",
            )
        ],
        recommended_mitigations=["Rebuild recruiter pipeline"],
    )
    assembler = EvidenceAssembler()
    dossier = assembler.assemble_dossier(assessment, signal_sources=["AUDIT_LEDGER_2024"])

    # 1. Credentials absent -> truthful DETERMINISTIC_FALLBACK
    reasoner_no_key = LLMStructuredReasoner(api_key="")
    status_no_key = reasoner_no_key.get_gemini_status()
    assert status_no_key.gemini_available is False
    assert status_no_key.credentials_present is False
    assert status_no_key.generation_mode == "DETERMINISTIC_FALLBACK"

    rep_fallback = reasoner_no_key.generate_narrative(dossier)
    assert rep_fallback.generation_mode == "DETERMINISTIC_FALLBACK"
    assert rep_fallback.gemini_live_used is False
    assert rep_fallback.fallback_reason is not None and "GEMINI_API_KEY" in rep_fallback.fallback_reason
    assert rep_fallback.what_is_happening != ""
    assert len(rep_fallback.why) > 0
    assert len(rep_fallback.evidence) == 1
    assert rep_fallback.what_could_happen_next != ""
    assert len(rep_fallback.what_should_leadership_investigate) > 0

    # 2. Credentials present and Gemini call succeeds -> LIVE_GEMINI
    reasoner_with_key = LLMStructuredReasoner(api_key="AIzaSyFakeTestKeyForUnitVerification", model_name="gemini-2.5-flash")
    status_with_key = reasoner_with_key.get_gemini_status()
    assert status_with_key.gemini_available is True
    assert status_with_key.credentials_present is True
    assert status_with_key.generation_mode == "LIVE_GEMINI"

    valid_gemini_payload = {
        "institution_id": "INST_P5_MODE",
        "risk_level": "HIGH",
        "composite_risk_index": 0.68,
        "executive_summary": "In AY 2024, Placements (CSE) dropped to 42.0 against baseline 82.0 (CRI 0.68).",
        "root_causes": ["Placement percentage fell to 42.0 vs baseline 82.0 (Z-score -2.95) in 2024."],
        "prioritized_actions": ["Rebuild recruiter pipeline"],
        "investigation_priorities": ["Audit CSE placement ledger for AY 2024."],
        "audit_provenance_summary": "Grounded in AUDIT_LEDGER_2024.",
    }
    mock_resp = MagicMock()
    mock_resp.text = json.dumps(valid_gemini_payload)

    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        rep_live = reasoner_with_key.generate_narrative(dossier)
        assert rep_live.generation_mode == "LIVE_GEMINI"
        assert rep_live.gemini_live_used is True
        assert rep_live.model_used == "gemini-2.5-flash"
        assert rep_live.fallback_reason is None

    # 3. Credentials present but Gemini raises an exception -> truthful fallback
    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError("Quota exceeded")
        mock_client_cls.return_value = mock_client

        rep_err_fb = reasoner_with_key.generate_narrative(dossier)
        assert rep_err_fb.generation_mode == "DETERMINISTIC_FALLBACK"
        assert rep_err_fb.gemini_live_used is False
        assert "RuntimeError" in (rep_err_fb.fallback_reason or "")


def test_phase5_hallucination_resistance_and_deterministic_override_guard():
    """
    Verify that Gemini cannot silently override deterministic CRI, risk_level, or anomaly numbers,
    and that unverified numeric fabrications are caught and flagged by the hallucination guard
    in both LLMStructuredReasoner and EvidenceGroundedInvestigationEngine.
    """
    assessment = CrisisAssessment(
        institution_id="INST_P5_GUARD",
        composite_risk_index=0.81,
        risk_level="CRITICAL",
        primary_driving_signal="Severe Placement Collapse",
        confidence_score=0.95,
        anomalies_detected=[
            SignalAnomaly(
                signal_name="Placements (ECE)",
                academic_year=2024,
                metric_name="placement_percentage",
                observed_value=31.0,
                baseline_value=79.5,
                deviation_zscore=-3.60,
                severity="CRITICAL",
                description="ECE placements collapsed to 31.0%.",
            )
        ],
        recommended_mitigations=["Immediate placement audit"],
    )
    assembler = EvidenceAssembler()
    dossier = assembler.assemble_dossier(assessment, signal_sources=["VERIFIED_DB"])

    # Simulate a hallucinating LLM response that tries to overwrite CRI (0.81 -> 0.12),
    # overwrite risk_level (CRITICAL -> LOW), and invent an unverified number (99.99%)
    hallucinated_payload = {
        "institution_id": "WRONG_INST_ID",
        "risk_level": "LOW",
        "composite_risk_index": 0.12,
        "executive_summary": "Everything is fine with a fabricated placement rate of 99.99% in 2024.",
        "root_causes": [
            "Hallucinated claim: research grant revenue rose by 777.42 crores.",
            "Verified claim: In 2024, ECE placement_percentage observed 31.0 vs baseline 79.5.",
        ],
        "prioritized_actions": ["None needed"],
        "investigation_priorities": ["Audit ECE placements"],
        "audit_provenance_summary": "LLM output",
    }
    mock_resp = MagicMock()
    mock_resp.text = json.dumps(hallucinated_payload)

    reasoner = LLMStructuredReasoner(api_key="AIzaSyFakeKeyForGuardTest")
    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        result = reasoner.generate_narrative(dossier)

        # Deterministic ground-truth values MUST prevail over Gemini's hallucinated values
        assert result.institution_id == "INST_P5_GUARD"
        assert result.risk_level == "CRITICAL"
        assert result.composite_risk_index == 0.81
        assert result.hallucination_guard_applied is True

        overrides = result.hallucination_guard_report["deterministic_overrides_applied"]
        assert len(overrides) == 3  # institution_id, risk_level, composite_risk_index

        flagged = result.hallucination_guard_report["unverified_claims_flagged"]
        assert len(flagged) >= 2  # 99.99 in summary and 777.42 in why[0]
        assert "[UNCERTAIN — UNVERIFIED NUMERIC CLAIM]" in result.why[0]
        assert any(c.is_uncertain for c in result.grounded_claims)

    # Also test hallucination guard on EvidenceGroundedInvestigationEngine
    intel_engine = InstitutionalIntelligenceEngine()
    intel_report = intel_engine.analyze_institution(
        institution_id="INST_P5_GUARD",
        cet_history=[],
        admissions_history=[],
        placements_history=[],
        dynamic_signals=[],
    )
    inv_engine = EvidenceGroundedInvestigationEngine(api_key="AIzaSyFakeKeyForGuardTest")
    bad_inv_resp = MagicMock()
    bad_inv_resp.text = json.dumps({
        "finding": "Placements fell because 888.88% of recruiters relocated to Mars.",
        "potential_contributing_factors": ["Recruiter shift"],
        "alternative_explanations": ["Cyclical hiring"],
    })
    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = bad_inv_resp
        mock_client_cls.return_value = mock_client

        inv_result = inv_engine.investigate_question(
            institution_id="INST_P5_GUARD",
            question="Why did placements fall?",
            dossier=dossier,
            intel_report=intel_report,
        )
        # Must reject the unverified 888.88% hallucination and revert to deterministic finding
        assert "888.88" not in inv_result.finding
        assert len(inv_result.hallucination_guard_report["unverified_claims_flagged"]) >= 1
