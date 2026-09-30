"""
End-to-End Validation Test Suite for all 10 AI CRISS Institutional Scenarios.
Verifies the complete pipeline:
INGESTION -> VALIDATION -> NORMALIZATION -> FEATURES -> INTELLIGENCE -> RISK
-> PREDICTION -> EVIDENCE -> LLM EXPLANATION -> REPORT (JSON + PDF) -> DASHBOARD
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.db.models import Base
from src.db.session import get_db_session
from src.engine.synthetic_generator import SyntheticDataGenerator
from src.adapters.json_adapter import JSONDictionaryAdapter
from src.engine.features import extract_department_features
from src.engine.crisis_scorer import CrisisIntelligenceEngine
from src.engine.predictor import TrajectoryPredictor
from src.engine.evidence import EvidenceAssembler
from src.engine.llm_reasoner import LLMStructuredReasoner
from src.reporting.pdf_generator import generate_crisis_pdf


def _run_pipeline(inst_id: str, scenario: str, num_years: int = 5):
    raw = SyntheticDataGenerator.generate_scenario(inst_id, scenario, 2020, num_years)
    parsed = JSONDictionaryAdapter().parse(raw)
    cet, adm, plc = parsed["cet_ranking"], parsed["admissions"], parsed["placements"]

    fv = extract_department_features(cet, adm, plc)
    assert fv is not None

    engine = CrisisIntelligenceEngine()
    assessment = engine.evaluate_institution(inst_id, cet, adm, plc)

    predictor = TrajectoryPredictor()
    traj_sq = predictor.predict_from_feature_vector(assessment.composite_risk_index, fv, years_forward=3)
    traj_iv = predictor.simulate_from_feature_vector(
        assessment.composite_risk_index,
        fv,
        {"placement_boost": 8.0, "vacancy_rate_reduction": 0.10},
        years_forward=3
    )

    assembler = EvidenceAssembler()
    dossier = assembler.assemble_dossier(assessment, signal_sources=[f"SYNTHETIC_{scenario}"])

    reasoner = LLMStructuredReasoner(api_key=None)
    narrative = reasoner.generate_narrative(dossier)
    assert narrative.composite_risk_index == assessment.composite_risk_index
    assert narrative.risk_level == assessment.risk_level

    pdf_bytes = generate_crisis_pdf(dossier, narrative, traj_sq)
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1500

    return fv, assessment, traj_sq, traj_iv, dossier, narrative, pdf_bytes


def test_scenario_01_healthy():
    fv, assessment, traj_sq, _, dossier, narrative, _ = _run_pipeline("SCEN_01_HEALTHY", "HEALTHY")
    assert assessment.risk_level == "LOW"
    assert assessment.composite_risk_index < 0.20
    assert len(assessment.anomalies_detected) == 0
    assert dossier.total_anomalies == 0


def test_scenario_02_admissions_decline():
    fv, assessment, traj_sq, _, dossier, narrative, _ = _run_pipeline("SCEN_02_ADM", "ADMISSIONS_DECLINE")
    assert assessment.risk_level in ("HIGH", "CRITICAL")
    assert assessment.composite_risk_index >= 0.60
    assert any(a.signal_name == "Admissions" for a in assessment.anomalies_detected)
    assert fv.vacancy_rate_slope > 0.05


def test_scenario_03_placement_deterioration():
    fv, assessment, traj_sq, _, dossier, narrative, _ = _run_pipeline("SCEN_03_PLC", "PLACEMENT_DETERIORATION")
    assert assessment.risk_level in ("HIGH", "CRITICAL")
    assert assessment.composite_risk_index >= 0.55
    assert any(a.signal_name == "Placements" for a in assessment.anomalies_detected)
    assert fv.placement_pct_slope < -5.0


def test_scenario_04_ranking_deterioration():
    fv, assessment, traj_sq, _, dossier, narrative, _ = _run_pipeline("SCEN_04_RNK", "RANKING_DETERIORATION")
    assert assessment.risk_level in ("HIGH", "CRITICAL")
    assert assessment.composite_risk_index >= 0.50
    assert any("Ranking" in a.signal_name for a in assessment.anomalies_detected)
    assert fv.closing_rank_slope > 1000.0


def test_scenario_05_cross_signal_crisis():
    """
    Ranking improves and Admissions grow, while Placements collapse.
    Verifies AI CRISS detects the cross-signal divergence rather than treating signals in isolation.
    """
    fv, assessment, traj_sq, _, dossier, narrative, _ = _run_pipeline("SCEN_05_CROSS", "CROSS_SIGNAL_CRISIS")
    assert fv.closing_rank_slope < 0.0  # Ranking improved (cutoff rank decreased)
    assert fv.vacancy_rate_current < 0.05  # Admissions near 100% full
    assert fv.placement_pct_current < 25.0  # Placements collapsed
    assert fv.intake_to_placed_divergence >= 0.50
    assert any("Cross-Signal" in a.signal_name for a in assessment.anomalies_detected)
    assert "Cross-Signal" in assessment.primary_driving_signal
    assert assessment.risk_level in ("HIGH", "CRITICAL")
    assert assessment.composite_risk_index >= 0.60


def test_scenario_06_cascading_crisis():
    fv, assessment, traj_sq, traj_iv, dossier, narrative, _ = _run_pipeline("SCEN_06_CASCADING", "CASCADING_CRISIS")
    assert assessment.risk_level == "CRITICAL"
    assert assessment.composite_risk_index >= 0.85
    assert len(assessment.anomalies_detected) >= 3
    assert traj_iv[-1].projected_cri <= traj_sq[-1].projected_cri


def test_scenario_07_gradual_decline():
    fv, assessment, traj_sq, _, dossier, narrative, _ = _run_pipeline("SCEN_07_GRADUAL", "GRADUAL_DECLINE")
    assert assessment.risk_level in ("MEDIUM", "HIGH", "CRITICAL")
    assert assessment.composite_risk_index >= 0.45
    assert fv.vacancy_rate_slope > 0.0
    assert fv.placement_pct_slope < 0.0
    assert fv.closing_rank_slope > 0.0


def test_scenario_08_sudden_crisis():
    fv, assessment, traj_sq, _, dossier, narrative, _ = _run_pipeline("SCEN_08_SUDDEN", "SUDDEN_CRISIS")
    assert assessment.risk_level == "CRITICAL"
    assert assessment.composite_risk_index >= 0.75
    assert len(assessment.anomalies_detected) >= 3


def test_scenario_09_recovery():
    fv, assessment, traj_sq, _, dossier, narrative, _ = _run_pipeline("SCEN_09_RECOVERY", "RECOVERY")
    assert assessment.risk_level == "LOW"
    assert assessment.composite_risk_index < 0.25
    assert fv.vacancy_rate_slope < 0.0  # Vacancy decreased over time
    assert fv.placement_pct_slope > 0.0  # Placements improved over time


def test_scenario_10_noisy_missing_data():
    fv, assessment, traj_sq, _, dossier, narrative, _ = _run_pipeline("SCEN_10_SPARSE", "NOISY_MISSING_DATA")
    assert fv.sparse_flag is True  # Fewer than 3 observations
    assert assessment.confidence_score == 0.75  # Lowered confidence for sparse history
    assert 0.0 <= assessment.composite_risk_index <= 1.0
