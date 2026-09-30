"""
CIP Phase 4 Verification Suite:
Tests for Explainable Forecasting, What-If Analysis, Institutional Memory,
Prediction/Outcome Learning Loop, User Feedback, and Institution-Specific Baselines.
"""

import pytest
from httpx import AsyncClient, ASGITransport

from src.api.main import app
from src.db.session import init_db
from src.contracts import (
    ProvenanceMetadata,
    AdmissionsSignal,
    PlacementsSignal,
    CETRankingSignal,
    ForecastStatus,
    MemoryCategory,
    FeedbackVerdict,
)
from src.engine.explainable_forecasting import ExplainableForecastingAndMemoryEngine


def _make_provenance(year: int) -> ProvenanceMetadata:
    return ProvenanceMetadata(
        source_id=f"TEST_SRC_{year}",
        source_type="institutional_export",
        confidence_score=0.95,
    )


def _build_multi_year_signals(inst_id: str, years: list[int]):
    cet_list = []
    adm_list = []
    plc_list = []
    for idx, yr in enumerate(years):
        prov = _make_provenance(yr)
        cet_list.append(
            CETRankingSignal(
                institution_id=inst_id,
                academic_year=yr,
                department="CSE",
                quota_category="General",
                opening_rank=5000 + idx * 1500,
                closing_rank=12000 + idx * 4500,
                percentile_cutoff=max(25.0, 88.0 - idx * 12.0),
                provenance=prov,
            )
        )
        enrolled = max(40, 114 - idx * 22)
        vacancy = 120 - enrolled
        adm_list.append(
            AdmissionsSignal(
                institution_id=inst_id,
                academic_year=yr,
                department="CSE",
                sanctioned_intake=120,
                enrolled_count=enrolled,
                vacancy_count=vacancy,
                vacancy_rate=round(vacancy / 120.0, 4),
                provenance=prov,
            )
        )
        placed = max(25, 88 - idx * 20)
        plc_list.append(
            PlacementsSignal(
                institution_id=inst_id,
                academic_year=yr,
                graduation_year=yr,
                department="CSE",
                eligible_students=100,
                placed_students=placed,
                placement_percentage=float(placed),
                median_salary_lpa=round(max(3.2, 6.5 - idx * 0.8), 2),
                max_salary_lpa=14.0,
                unplaced_count=100 - placed,
                provenance=prov,
            )
        )
    return cet_list, adm_list, plc_list


async def _get_auth_headers(ac: AsyncClient, email: str = "phase4_lead@cip.org") -> dict:
    await init_db()
    signup_res = await ac.post(
        "/api/v1/auth/signup",
        json={
            "email": email,
            "password": "Phase4SecurePassword!123",
            "full_name": "Phase 4 Lead Engineer",
        },
    )
    if signup_res.status_code in (200, 201):
        token = signup_res.json()["access_token"]
    else:
        login_res = await ac.post(
            "/api/v1/auth/login",
            data={"username": email, "password": "Phase4SecurePassword!123"},
        )
        assert login_res.status_code == 200
        token = login_res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_sufficient_history_forecast():
    """
    1. Sufficient-history forecast:
       Verify that an institution with >= 2 historical periods exposes all required
       CIP Phase 4 forecast attributes: prediction, horizon, method_actually_used,
       input_signals, historical_evidence, data_coverage, confidence, limitations,
       alternative_explanations, trajectory_drivers.
    """
    engine = ExplainableForecastingAndMemoryEngine()
    cet, adm, plc = _build_multi_year_signals("INST_P4_SUFFICIENT", [2021, 2022, 2023, 2024])

    forecast = engine.generate_explainable_forecast(
        institution_id="INST_P4_SUFFICIENT",
        cet_history=cet,
        admissions_history=adm,
        placements_history=plc,
        years_forward=3,
    )

    assert forecast.status == ForecastStatus.SUFFICIENT_EVIDENCE
    assert forecast.insufficient_evidence_reason is None
    assert forecast.prediction is not None
    assert len(forecast.prediction) == 3
    assert [p.target_period for p in forecast.prediction] == [2025, 2026, 2027]
    assert "2025 to 2027" in forecast.horizon
    assert "Autoregressive Momentum Model" in forecast.method_actually_used
    assert "current_cri" in forecast.input_signals
    assert "feature_slopes" in forecast.input_signals
    assert len(forecast.historical_evidence) >= 2
    assert forecast.data_coverage.is_sufficient is True
    assert forecast.data_coverage.distinct_periods == 4
    assert 0.35 <= forecast.confidence <= 0.95
    assert len(forecast.limitations) >= 2
    assert len(forecast.alternative_explanations) >= 1
    assert len(forecast.trajectory_drivers) >= 3
    worsening_drivers = [d for d in forecast.trajectory_drivers if d.direction == "WORSENING_RISK"]
    assert len(worsening_drivers) >= 2


@pytest.mark.asyncio
async def test_insufficient_history_forecast():
    """
    2. Insufficient-history forecast:
       Verify that when history has < 2 distinct periods (e.g., 1 period or 0 periods),
       the engine returns an explicit INSUFFICIENT_EVIDENCE state with prediction=None
       instead of fabricating a fake precise prediction.
    """
    engine = ExplainableForecastingAndMemoryEngine()
    cet_1yr, adm_1yr, plc_1yr = _build_multi_year_signals("INST_P4_SPARSE", [2024])

    forecast_1yr = engine.generate_explainable_forecast(
        institution_id="INST_P4_SPARSE",
        cet_history=cet_1yr,
        admissions_history=adm_1yr,
        placements_history=plc_1yr,
        years_forward=3,
    )

    assert forecast_1yr.status == ForecastStatus.INSUFFICIENT_EVIDENCE
    assert forecast_1yr.prediction is None
    assert forecast_1yr.confidence == 0.0
    assert forecast_1yr.method_actually_used == "NONE_INSUFFICIENT_EVIDENCE"
    assert forecast_1yr.data_coverage.is_sufficient is False
    assert forecast_1yr.data_coverage.distinct_periods == 1
    assert "Insufficient historical evidence" in (forecast_1yr.insufficient_evidence_reason or "")
    assert forecast_1yr.trajectory_drivers == []


@pytest.mark.asyncio
async def test_forecast_explanation_why_did_prediction_change():
    """
    3. Forecast explanation ("Why did the prediction change?"):
       Verify that when new period signals or changed slopes alter the forecast,
       why_did_prediction_change identifies the exact changed inputs (current_cri,
       vacancy_rate_slope, placement_pct_slope, closing_rank_slope) and their
       deterministic mathematical impact.
    """
    engine = ExplainableForecastingAndMemoryEngine()
    cet_3yr, adm_3yr, plc_3yr = _build_multi_year_signals("INST_P4_DELTA", [2021, 2022, 2023])
    prior_forecast = engine.generate_explainable_forecast(
        institution_id="INST_P4_DELTA",
        cet_history=cet_3yr,
        admissions_history=adm_3yr,
        placements_history=plc_3yr,
        years_forward=3,
    )

    cet_4yr, adm_4yr, plc_4yr = _build_multi_year_signals("INST_P4_DELTA", [2021, 2022, 2023, 2024])
    # Replace 2024 with a sharper deterioration
    prov_24 = _make_provenance(2024)
    adm_4yr[-1] = AdmissionsSignal(
        institution_id="INST_P4_DELTA",
        academic_year=2024,
        department="CSE",
        sanctioned_intake=120,
        enrolled_count=30,
        vacancy_count=90,
        vacancy_rate=0.75,
        provenance=prov_24,
    )
    plc_4yr[-1] = PlacementsSignal(
        institution_id="INST_P4_DELTA",
        academic_year=2024,
        graduation_year=2024,
        department="CSE",
        eligible_students=100,
        placed_students=15,
        placement_percentage=15.0,
        median_salary_lpa=3.0,
        max_salary_lpa=10.0,
        unplaced_count=85,
        provenance=prov_24,
    )

    updated_forecast = engine.generate_explainable_forecast(
        institution_id="INST_P4_DELTA",
        cet_history=cet_4yr,
        admissions_history=adm_4yr,
        placements_history=plc_4yr,
        years_forward=3,
        previous_prediction_payload=prior_forecast.model_dump(mode="json"),
    )

    why = updated_forecast.why_did_prediction_change
    assert why is not None
    assert why.has_previous_prediction is True
    assert 2024 in why.newly_added_periods
    assert len(why.changed_inputs) >= 2
    changed_names = {c.input_name for c in why.changed_inputs}
    assert "current_cri" in changed_names or "vacancy_rate_slope" in changed_names or "placement_pct_slope" in changed_names
    assert why.terminal_cri_delta is not None
    assert "changed inputs" in why.summary.lower()


@pytest.mark.asyncio
async def test_what_if_analysis_and_ui_rename():
    """
    4. What-If Analysis:
       - Verify UI renames 'Forward Trajectory Simulator' to 'What-If Analysis'.
       - Verify /what-if and /simulate expose baseline, intervention, projected_trajectory,
         estimated_risk_change, and reason_for_change.
       - Verify every user control maps to a real mathematical model input and unknown/decorative
         controls are rejected with HTTP 422.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        dash_res = await ac.get("/dashboard/")
        assert dash_res.status_code == 200
        html = dash_res.text
        assert "What-If Analysis" in html
        assert "Forward Trajectory Simulator" not in html

        headers = await _get_auth_headers(ac, email="phase4_whatif@cip.org")
        seed_res = await ac.post(
            "/api/v1/ingest/synthetic",
            json={"institution_id": "INST_P4_WHATIF", "scenario": "CASCADING_CRISIS"},
            headers=headers,
        )
        assert seed_res.status_code == 200

        wi_res = await ac.post(
            "/api/v1/institutions/INST_P4_WHATIF/what-if",
            json={
                "years_forward": 3,
                "intervention_effects": {
                    "placement_boost": 25.0,
                    "vacancy_rate_reduction": 0.25,
                    "closing_rank_stabilization": 25.0,
                },
            },
            headers=headers,
        )
        assert wi_res.status_code == 200
        wi = wi_res.json()
        assert wi["analysis_type"] == "What-If Analysis"
        assert "baseline" in wi
        assert "intervention" in wi
        assert "projected_trajectory" in wi
        assert "estimated_risk_change" in wi
        assert "reason_for_change" in wi
        assert wi["estimated_risk_change"]["risk_reduction_achieved"] > 0
        assert wi["estimated_risk_change"]["direction"] == "RISK_REDUCED"
        mapped_controls = {m["control_key"] for m in wi["intervention"]["control_mappings"]}
        assert mapped_controls == {"placement_boost", "vacancy_rate_reduction", "closing_rank_stabilization"}


        bad_res = await ac.post(
            "/api/v1/institutions/INST_P4_WHATIF/what-if",
            json={
                "years_forward": 3,
                "intervention_effects": {
                    "decorative_marketing_buzz_slider": 99.0,
                },
            },
            headers=headers,
        )
        assert bad_res.status_code == 422
        assert "Unsupported or decorative What-If control" in bad_res.json()["detail"]


@pytest.mark.asyncio
async def test_memory_persistence_outcome_comparison_and_feedback():
    """
    5, 6, 7 & 8:
    - Memory persistence across all 7 categories (observed_fact, analysis, inference,
      prediction, outcome, user_feedback, unknown) and 8 collections (historical_signals,
      baselines, previous_analyses, predictions, interventions, outcomes,
      validated_rejected_hypotheses, unresolved_questions).
    - Prediction/outcome comparison learning loop (Prediction -> later observation ->
      outcome comparison -> prediction accuracy -> institutional learning).
    - User feedback (Confirmed / Incorrect / Insufficient Evidence) stored strictly as
      user_feedback and never blindly converted into observed_fact.
    - Institution-specific baseline preserved in memory and never fabricated when sparse.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        headers = await _get_auth_headers(ac, email="phase4_memory@cip.org")
        inst_id = "INST_P4_MEMORY"

        seed_res = await ac.post(
            "/api/v1/ingest/synthetic",
            json={"institution_id": inst_id, "scenario": "CASCADING_CRISIS"},
            headers=headers,
        )
        assert seed_res.status_code == 200

        eval_res = await ac.get(f"/api/v1/institutions/{inst_id}/evaluate", headers=headers)
        assert eval_res.status_code == 200

        sim_res = await ac.post(
            f"/api/v1/institutions/{inst_id}/what-if",
            json={
                "years_forward": 3,
                "intervention_effects": {"placement_boost": 6.0, "vacancy_rate_reduction": 0.10},
            },
            headers=headers,
        )
        assert sim_res.status_code == 200

        fc_res = await ac.get(f"/api/v1/institutions/{inst_id}/forecast", headers=headers)
        assert fc_res.status_code == 200
        fc_data = fc_res.json()
        assert fc_data["status"] == "SUFFICIENT_EVIDENCE"
        assert len(fc_data["prediction"]) == 3

        why_res = await ac.get(f"/api/v1/institutions/{inst_id}/forecast/why-changed", headers=headers)
        assert why_res.status_code == 200
        assert "summary" in why_res.json()

        mem_res = await ac.get(f"/api/v1/institutions/{inst_id}/memory", headers=headers)
        assert mem_res.status_code == 200
        mem = mem_res.json()

        cats = mem["entries_by_category"]
        for expected_cat in [
            "observed_fact",
            "analysis",
            "inference",
            "prediction",
            "outcome",
            "user_feedback",
            "unknown",
        ]:
            assert expected_cat in cats

        assert len(cats["observed_fact"]) > 0
        assert len(cats["analysis"]) > 0
        assert len(cats["inference"]) > 0
        assert len(cats["prediction"]) > 0
        assert len(cats["outcome"]) > 0
        assert len(cats["unknown"]) > 0

        assert len(mem["historical_signals"]) > 0
        assert len(mem["baselines"]) > 0
        established_baselines = [b for b in mem["baselines"] if b["baseline_status"] == "ESTABLISHED"]
        assert len(established_baselines) > 0
        assert len(mem["previous_analyses"]) > 0
        assert len(mem["predictions"]) > 0
        assert len(mem["interventions"]) > 0
        assert len(mem["outcomes"]) > 0
        assert len(mem["validated_rejected_hypotheses"]) > 0
        assert len(mem["unresolved_questions"]) > 0

        out_res = await ac.post(
            f"/api/v1/institutions/{inst_id}/memory/outcomes",
            json={
                "target_academic_year": 2025,
                "metric_name": "composite_risk_index",
                "predicted_value": fc_data["prediction"][0]["projected_cri"],
                "later_observed_value": max(0.0, fc_data["prediction"][0]["projected_cri"] - 0.04),
                "confidence_band_low": fc_data["prediction"][0]["confidence_band_low"],
                "confidence_band_high": fc_data["prediction"][0]["confidence_band_high"],
                "notes": "Post-intervention 2025 placement audit observed CRI reduction.",
            },
            headers=headers,
        )
        assert out_res.status_code == 200
        out_data = out_res.json()
        assert out_data["target_academic_year"] == 2025
        assert out_data["within_confidence_band"] is True
        assert out_data["prediction_accuracy"] >= 0.90
        assert (
            "longitudinal" in out_data["institutional_learning"].lower()
            or "calibrated" in out_data["institutional_learning"].lower()
        )

        observed_facts_before = len(mem["entries_by_category"]["observed_fact"])
        user_feedback_before = len(mem["entries_by_category"]["user_feedback"])
        first_hyp_id = mem["validated_rejected_hypotheses"][0]["hypothesis_id"]

        fb1 = await ac.post(
            f"/api/v1/institutions/{inst_id}/memory/feedback",
            json={
                "target_id": first_hyp_id,
                "target_category": "inference",
                "verdict": "Confirmed",
                "reviewer_notes": "Confirmed by Dean of Placements audit.",
            },
            headers=headers,
        )
        assert fb1.status_code == 200
        fb1_data = fb1.json()
        assert fb1_data["verdict"] == "Confirmed"
        assert fb1_data["stored_category"] == "user_feedback"
        assert fb1_data["converted_to_observed_fact"] is False

        fb2 = await ac.post(
            f"/api/v1/institutions/{inst_id}/memory/feedback",
            json={
                "target_id": "hyp_custom_rejected",
                "target_category": "inference",
                "verdict": "Incorrect",
                "reviewer_notes": "Hypothesis rejected after reviewing department records.",
            },
            headers=headers,
        )
        assert fb2.status_code == 200
        assert fb2.json()["verdict"] == "Incorrect"
        assert fb2.json()["converted_to_observed_fact"] is False

        fb3 = await ac.post(
            f"/api/v1/institutions/{inst_id}/memory/feedback",
            json={
                "target_id": "hyp_custom_insufficient",
                "target_category": "inference",
                "verdict": "Insufficient Evidence",
                "reviewer_notes": "Awaiting next semester attendance logs.",
            },
            headers=headers,
        )
        assert fb3.status_code == 200
        assert fb3.json()["verdict"] == "Insufficient Evidence"
        assert fb3.json()["converted_to_observed_fact"] is False

        mem_after = (await ac.get(f"/api/v1/institutions/{inst_id}/memory", headers=headers)).json()
        assert len(mem_after["entries_by_category"]["user_feedback"]) == user_feedback_before + 3
        assert len(mem_after["entries_by_category"]["observed_fact"]) == observed_facts_before


        hyp_by_id = {h["hypothesis_id"]: h for h in mem_after["validated_rejected_hypotheses"]}
        assert hyp_by_id[first_hyp_id]["status"] == "VALIDATED"
        assert hyp_by_id["hyp_custom_rejected"]["status"] == "REJECTED"
        assert hyp_by_id["hyp_custom_insufficient"]["status"] == "INSUFFICIENT_EVIDENCE"


@pytest.mark.asyncio
async def test_api_insufficient_history_forecast_and_baseline():
    """
    Verify via API that an institution with only 1 ingested time period:
    - Returns status='INSUFFICIENT_EVIDENCE' and prediction=None on /forecast
    - Does not fabricate historical baselines on /baselines (baseline_status='INSUFFICIENT_HISTORY')
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        headers = await _get_auth_headers(ac, email="phase4_sparse@cip.org")
        inst_id = "INST_P4_SINGLE_PERIOD"

        csv_bytes = (
            b"academic_year,department,placement_rate,faculty_attrition_rate\n"
            b"2024,CSE,71.5,14.2\n"
        )
        up_res = await ac.post(
            "/api/v1/ingest/upload",
            data={"institution_id": inst_id},
            files={"file": ("single_year_2024.csv", csv_bytes, "text/csv")},
            headers=headers,
        )
        assert up_res.status_code == 200

        fc_res = await ac.get(f"/api/v1/institutions/{inst_id}/forecast", headers=headers)
        assert fc_res.status_code == 200
        fc = fc_res.json()
        assert fc["status"] == "INSUFFICIENT_EVIDENCE"
        assert fc["prediction"] is None
        assert fc["data_coverage"]["distinct_periods"] == 1
        assert fc["data_coverage"]["is_sufficient"] is False

        bl_res = await ac.get(f"/api/v1/institutions/{inst_id}/baselines", headers=headers)
        assert bl_res.status_code == 200
        baselines = bl_res.json()
        assert len(baselines) > 0
        for b in baselines:
            assert b["baseline_status"] == "INSUFFICIENT_HISTORY"
            assert b["baseline_mean"] is None
