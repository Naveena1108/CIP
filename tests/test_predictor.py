"""Tests for ACT-02: TrajectoryPredictor and FeatureVector integration."""

import pytest
from src.engine.predictor import TrajectoryPredictor, TrajectoryPoint
from src.engine.features import FeatureVector


@pytest.fixture
def predictor() -> TrajectoryPredictor:
    return TrajectoryPredictor()


def test_status_quo_stable(predictor: TrajectoryPredictor) -> None:
    """Given CRI=0.3 and flat slopes (all 0.0), trajectory should stay near 0.3."""
    flat_slopes = {
        "vacancy_rate_slope": 0.0,
        "placement_pct_slope": 0.0,
        "closing_rank_slope": 0.0,
    }
    trajectory = predictor.predict_trajectory(current_cri=0.3, feature_slopes=flat_slopes, years_forward=3)

    assert len(trajectory) == 3
    for pt in trajectory:
        assert pt.scenario == "STATUS_QUO"
        assert pt.projected_cri == pytest.approx(0.3, abs=1e-6)


def test_worsening_trajectory(predictor: TrajectoryPredictor) -> None:
    """Negative placement slope drives CRI upward over 3 years."""
    worsening_slopes = {
        "vacancy_rate_slope": 0.0,
        "placement_pct_slope": -5.0,
        "closing_rank_slope": 0.0,
    }
    trajectory = predictor.predict_trajectory(current_cri=0.5, feature_slopes=worsening_slopes, years_forward=3)

    assert len(trajectory) == 3
    for pt in trajectory:
        assert pt.projected_cri > 0.5
    cris = [pt.projected_cri for pt in trajectory]
    assert cris == sorted(cris)


def test_intervention_reduces_risk(predictor: TrajectoryPredictor) -> None:
    """Applying intervention_effects should produce lower CRI than status_quo."""
    worsening_slopes = {
        "vacancy_rate_slope": 0.0,
        "placement_pct_slope": -5.0,
        "closing_rank_slope": 0.0,
    }
    intervention_effects = {
        "vacancy_rate_reduction": 0.10,
        "placement_boost": 5.0,
    }
    sq = predictor.predict_trajectory(
        current_cri=0.5, feature_slopes=worsening_slopes, years_forward=3,
    )
    iv = predictor.simulate_intervention(
        current_cri=0.5,
        feature_slopes=worsening_slopes,
        intervention_effects=intervention_effects,
        years_forward=3,
    )

    for sq_pt, iv_pt in zip(sq, iv):
        assert iv_pt.scenario == "WITH_INTERVENTION"
        assert iv_pt.projected_cri < sq_pt.projected_cri


def test_cri_clamping(predictor: TrajectoryPredictor) -> None:
    """Starting near ceiling with worsening slopes, CRI should never exceed 1.0."""
    extreme_slopes = {
        "vacancy_rate_slope": 10.0,
        "placement_pct_slope": -10.0,
        "closing_rank_slope": 10.0,
    }
    trajectory = predictor.predict_trajectory(current_cri=0.95, feature_slopes=extreme_slopes, years_forward=3)

    for pt in trajectory:
        assert 0.0 <= pt.projected_cri <= 1.0
        assert 0.0 <= pt.confidence_band_low <= 1.0
        assert 0.0 <= pt.confidence_band_high <= 1.0
        assert pt.confidence_band_low <= pt.projected_cri <= pt.confidence_band_high


def test_feature_vector_integration(predictor: TrajectoryPredictor) -> None:
    """Verifies direct prediction and simulation from a FeatureVector."""
    fv = FeatureVector(
        institution_id="INST_01",
        department="CSE",
        academic_year=2024,
        vacancy_rate_current=0.25,
        vacancy_rate_slope=0.05,
        enrollment_delta_pct=-10.0,
        placement_pct_current=60.0,
        placement_pct_slope=-4.0,
        unplaced_ratio_current=0.40,
        closing_rank_current=22000.0,
        closing_rank_slope=1500.0,
        percentile_delta=-5.0,
        intake_to_placed_divergence=0.2,
        sparse_flag=False,
    )
    sq = predictor.predict_from_feature_vector(0.45, fv, years_forward=3)
    assert len(sq) == 3
    assert sq[-1].projected_cri > 0.45

    iv = predictor.simulate_from_feature_vector(
        0.45, fv, {"placement_boost": 6.0, "vacancy_rate_reduction": 0.05}, years_forward=3
    )
    assert len(iv) == 3
    assert iv[-1].projected_cri < sq[-1].projected_cri
