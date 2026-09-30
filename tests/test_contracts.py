import pytest
from src.contracts import (
    ProvenanceMetadata,
    CETRankingSignal,
    AdmissionsSignal,
    PlacementsSignal,
    CrisisAssessment,
    SignalAnomaly
)

def test_cet_ranking_contract():
    signal = CETRankingSignal(
        institution_id="INST_001",
        academic_year=2024,
        department="Computer Science",
        opening_rank=1200,
        closing_rank=4500,
        percentile_cutoff=95.5,
        provenance=ProvenanceMetadata(source_id="CET_OFFICIAL_2024", source_type="real_verified")
    )
    assert signal.institution_id == "INST_001"
    assert signal.closing_rank == 4500

def test_admissions_contract_validation():
    signal = AdmissionsSignal(
        institution_id="INST_001",
        academic_year=2024,
        department="Computer Science",
        sanctioned_intake=120,
        enrolled_count=100,
        vacancy_count=20,
        vacancy_rate=0.1667
    )
    assert signal.vacancy_count == 20

def test_admissions_contract_invalid_capacity():
    with pytest.raises(ValueError):
        AdmissionsSignal(
            institution_id="INST_001",
            academic_year=2024,
            department="Computer Science",
            sanctioned_intake=100,
            enrolled_count=120, # Exceeds capacity
            vacancy_count=0,
            vacancy_rate=0.0
        )

def test_placements_contract_validation():
    signal = PlacementsSignal(
        institution_id="INST_001",
        academic_year=2024,
        graduation_year=2024,
        department="Computer Science",
        eligible_students=100,
        placed_students=85,
        placement_percentage=85.0,
        median_salary_lpa=8.5,
        max_salary_lpa=24.0,
        unplaced_count=15
    )
    assert signal.placed_students == 85
    assert signal.unplaced_count == 15
