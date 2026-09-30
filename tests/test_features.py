"""Tests for Feature Engineering & Temporal Windowing (src/engine/features.py)."""

import pytest
from src.contracts import (
    ProvenanceMetadata,
    CETRankingSignal,
    AdmissionsSignal,
    PlacementsSignal
)
from src.engine.features import (
    extract_department_features,
    extract_institutional_features,
    _linear_slope,
    _pct_change,
    FeatureVector
)

PROVENANCE = ProvenanceMetadata(source_id="TEST", source_type="synthetic_generated")

def make_signals(years=[2022, 2023, 2024], intake=100, enrollments=[90, 80, 70], placements=[80, 60, 40], ranks=[10000, 15000, 25000]):
    adm = []
    plc = []
    cet = []
    for yr, enr, plc_cnt, rnk in zip(years, enrollments, placements, ranks):
        adm.append(AdmissionsSignal(
            institution_id="TEST_INST",
            academic_year=yr,
            department="CSE",
            sanctioned_intake=intake,
            enrolled_count=enr,
            vacancy_count=intake - enr,
            vacancy_rate=round((intake - enr) / intake, 4),
            provenance=PROVENANCE
        ))
        plc.append(PlacementsSignal(
            institution_id="TEST_INST",
            academic_year=yr,
            graduation_year=yr,
            department="CSE",
            eligible_students=enr,
            placed_students=plc_cnt,
            placement_percentage=round((plc_cnt / enr) * 100.0, 2),
            median_salary_lpa=5.0,
            max_salary_lpa=12.0,
            unplaced_count=enr - plc_cnt,
            provenance=PROVENANCE
        ))
        cet.append(CETRankingSignal(
            institution_id="TEST_INST",
            academic_year=yr,
            department="CSE",
            opening_rank=rnk - 1000,
            closing_rank=rnk,
            percentile_cutoff=90.0,
            provenance=PROVENANCE
        ))
    return cet, adm, plc

def test_linear_slope_math():
    # Flat
    assert _linear_slope([10.0, 10.0, 10.0]) == 0.0
    # Positive slope: +5 per step
    assert _linear_slope([10.0, 15.0, 20.0]) == 5.0
    # Negative slope: -2.5 per step
    assert _linear_slope([20.0, 17.5, 15.0]) == -2.5
    # Single point
    assert _linear_slope([10.0]) == 0.0

def test_pct_change_math():
    assert _pct_change(100.0, 150.0) == 50.0
    assert _pct_change(100.0, 50.0) == -50.0
    assert _pct_change(0.0, 0.0) == 0.0
    assert _pct_change(0.0, 50.0) == 100.0

def test_extract_department_features_basic():
    cet, adm, plc = make_signals()
    fv = extract_department_features(cet, adm, plc)
    assert fv is not None
    assert fv.institution_id == "TEST_INST"
    assert fv.department == "CSE"
    assert fv.academic_year == 2024
    assert fv.sparse_flag is False
    # Vacancy rates are 0.10, 0.20, 0.30 -> slope should be +0.10
    assert fv.vacancy_rate_current == 0.30
    assert pytest.approx(fv.vacancy_rate_slope, abs=0.01) == 0.10
    # Placement % is decreasing
    assert fv.placement_pct_slope < 0
    # Closing rank is worsening (+7500 per step)
    assert fv.closing_rank_slope > 0

def test_sparse_flag_for_short_history():
    # 2 years only -> sparse flag True
    cet, adm, plc = make_signals(years=[2023, 2024], enrollments=[80, 70], placements=[60, 40], ranks=[15000, 25000])
    fv = extract_department_features(cet, adm, plc)
    assert fv is not None
    assert fv.sparse_flag is True

def test_cross_signal_divergence():
    # Admissions enrollment flat (100 -> 100), but placements collapse (80 -> 20)
    cet, adm, plc = make_signals(
        years=[2023, 2024],
        intake=100,
        enrollments=[100, 100],
        placements=[80, 20],
        ranks=[10000, 10000]
    )
    fv = extract_department_features(cet, adm, plc)
    assert fv is not None
    # Intake growth = 0%, placed growth = -75%. Divergence = (0 - (-75)) / 100 = +0.75
    assert fv.intake_to_placed_divergence > 0.5

def test_extract_institutional_features_multi_dept():
    cet1, adm1, plc1 = make_signals()
    # Create second department ECE
    adm2 = [s.model_copy(update={"department": "ECE"}) for s in adm1]
    plc2 = [s.model_copy(update={"department": "ECE"}) for s in plc1]
    cet2 = [s.model_copy(update={"department": "ECE"}) for s in cet1]

    all_cet = cet1 + cet2
    all_adm = adm1 + adm2
    all_plc = plc1 + plc2

    dept_features = extract_institutional_features(all_cet, all_adm, all_plc)
    assert "CSE" in dept_features
    assert "ECE" in dept_features
    assert isinstance(dept_features["CSE"], FeatureVector)
    assert isinstance(dept_features["ECE"], FeatureVector)
