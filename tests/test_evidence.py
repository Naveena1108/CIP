"""Tests for Evidence & Audit Assembler (src/engine/evidence.py)."""

import pytest
from src.contracts import CrisisAssessment, SignalAnomaly, ProvenanceMetadata
from src.engine.evidence import EvidenceAssembler, EvidenceToken, InstitutionalDossier


def test_assemble_dossier_with_anomalies():
    assembler = EvidenceAssembler()
    assessment = CrisisAssessment(
        institution_id="INST_ALPHA",
        composite_risk_index=0.65,
        risk_level="HIGH",
        primary_driving_signal="Placement Collapse",
        confidence_score=0.92,
        anomalies_detected=[
            SignalAnomaly(
                signal_name="Placements",
                academic_year=2024,
                metric_name="placement_percentage",
                observed_value=24.5,
                baseline_value=78.0,
                deviation_zscore=-3.45,
                severity="CRITICAL",
                description="Severe placement collapse in 2024."
            ),
            SignalAnomaly(
                signal_name="Admissions",
                academic_year=2024,
                metric_name="enrolled_count",
                observed_value=55.0,
                baseline_value=120.0,
                deviation_zscore=-2.80,
                severity="HIGH",
                description="Enrollment deficit."
            )
        ],
        recommended_mitigations=["Overhaul placement training", "Review seat matrix"],
        provenance=ProvenanceMetadata(source_id="INSTITUTIONAL_WORKBOOK", source_type="institutional_export")
    )

    dossier = assembler.assemble_dossier(assessment, signal_sources=["EXCEL_SHEET_1", "EXCEL_SHEET_2"])

    assert isinstance(dossier, InstitutionalDossier)
    assert dossier.institution_id == "INST_ALPHA"
    assert dossier.risk_level == "HIGH"
    assert dossier.composite_risk_index == 0.65
    assert dossier.total_anomalies == 2
    assert len(dossier.evidence_tokens) == 2
    assert dossier.signal_coverage["placements"] is True
    assert dossier.signal_coverage["admissions"] is True
    assert dossier.signal_coverage["cet_ranking"] is False
    assert "EXCEL_SHEET_1" in dossier.provenance_chain
    assert len(dossier.recommended_mitigations) == 2


def test_assemble_dossier_empty_anomalies():
    assembler = EvidenceAssembler()
    assessment = CrisisAssessment(
        institution_id="INST_HEALTHY",
        composite_risk_index=0.15,
        risk_level="LOW",
        primary_driving_signal="Admissions",
        confidence_score=0.95,
        anomalies_detected=[],
        recommended_mitigations=[]
    )

    dossier = assembler.assemble_dossier(assessment)
    assert dossier.total_anomalies == 0
    assert len(dossier.evidence_tokens) == 0
    assert dossier.signal_coverage["placements"] is False
    assert dossier.signal_coverage["admissions"] is False
    assert dossier.signal_coverage["cet_ranking"] is False


def test_evidence_token_narratives():
    assembler = EvidenceAssembler()
    anomaly = SignalAnomaly(
        signal_name="CET / Ranking",
        academic_year=2024,
        metric_name="closing_rank",
        observed_value=45000.0,
        baseline_value=15000.0,
        deviation_zscore=3.12,
        severity="CRITICAL",
        description="Cutoff slipped to 45000."
    )
    assessment = CrisisAssessment(
        institution_id="INST_01",
        composite_risk_index=0.72,
        risk_level="CRITICAL",
        primary_driving_signal="Ranking Deterioration",
        confidence_score=0.90,
        anomalies_detected=[anomaly]
    )

    dossier = assembler.assemble_dossier(assessment)
    token = dossier.evidence_tokens[0]
    assert "45000" in token.narrative_fragment
    assert "15000" in token.narrative_fragment
    assert "+3.12" in token.narrative_fragment
    assert "CRITICAL" in token.narrative_fragment
