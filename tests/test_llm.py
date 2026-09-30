"""
Tests for LLM Structured Reasoner (src/engine/llm_reasoner.py).
Verifies official google-genai SDK integration, deterministic fallback,
mocked provider calls, failure tolerance, and mathematical invariant preservation.
"""

import json
from unittest.mock import MagicMock, patch
import pytest
from src.engine.evidence import EvidenceAssembler, InstitutionalDossier, EvidenceToken
from src.engine.llm_reasoner import LLMStructuredReasoner, ExecutiveNarrativeResponse


@pytest.fixture
def sample_crisis_dossier() -> InstitutionalDossier:
    return InstitutionalDossier(
        institution_id="INST_CRISIS_01",
        risk_level="CRITICAL",
        composite_risk_index=0.785,
        primary_threat="Placement Degradation",
        evidence_tokens=[
            EvidenceToken(
                signal_name="Placements",
                metric_name="placement_percentage",
                observed_value=15.2,
                baseline_value=72.0,
                deviation_zscore=-3.65,
                severity="CRITICAL",
                academic_year=2024,
                narrative_fragment="In AY 2024, Placements dropped below baseline: observed 15.2, historical baseline 72.0."
            )
        ],
        total_anomalies=1,
        provenance_chain=["INSTITUTIONAL_WORKBOOK"],
        recommended_mitigations=["Immediate recruiter outreach."]
    )


def test_build_prompt_with_tokens(sample_crisis_dossier: InstitutionalDossier):
    reasoner = LLMStructuredReasoner()
    prompt = reasoner.build_prompt(sample_crisis_dossier)
    assert "INST_CRISIS_01" in prompt
    assert "0.785" in prompt
    assert "15.2" in prompt
    assert "-3.65" in prompt


def test_fallback_when_api_key_absent(sample_crisis_dossier: InstitutionalDossier):
    """When GEMINI_API_KEY is None or empty, reasoner must use deterministic synthesis."""
    reasoner = LLMStructuredReasoner(api_key=None)
    narrative = reasoner.generate_narrative(sample_crisis_dossier)

    assert isinstance(narrative, ExecutiveNarrativeResponse)
    assert narrative.institution_id == "INST_CRISIS_01"
    assert narrative.risk_level == "CRITICAL"
    assert narrative.composite_risk_index == 0.785
    assert "Placement Degradation" in narrative.executive_summary
    assert len(narrative.root_causes) > 0


def test_successful_mocked_gemini_response(sample_crisis_dossier: InstitutionalDossier):
    """When Gemini API returns valid JSON adhering to schema, it is accepted."""
    mock_payload = {
        "institution_id": "INST_CRISIS_01",
        "risk_level": "CRITICAL",
        "composite_risk_index": 0.785,
        "executive_summary": "Gemini-generated briefing: Severe placement collapse detected.",
        "root_causes": ["Historical recruiter withdrawal."],
        "prioritized_actions": ["Emergency alumni placement drive."],
        "investigation_priorities": ["Audit placement office outreach."],
        "audit_provenance_summary": "Verified against institutional data."
    }

    mock_response = MagicMock()
    mock_response.text = json.dumps(mock_payload)

    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        mock_client_cls.return_value = mock_client

        reasoner = LLMStructuredReasoner(api_key="mock-test-key-12345")
        narrative = reasoner.generate_narrative(sample_crisis_dossier)

        assert narrative.executive_summary == "Gemini-generated briefing: Severe placement collapse detected."
        assert narrative.prioritized_actions == ["Emergency alumni placement drive."]
        assert narrative.composite_risk_index == 0.785


def test_gemini_cannot_alter_mathematical_cri(sample_crisis_dossier: InstitutionalDossier):
    """If the LLM attempts to output a modified CRI, the system enforces ground truth."""
    tampered_payload = {
        "institution_id": "INST_CRISIS_01",
        "risk_level": "LOW",              # Tampered by LLM
        "composite_risk_index": 0.100,     # Tampered by LLM
        "executive_summary": "Everything is fine.",
        "root_causes": [],
        "prioritized_actions": [],
        "investigation_priorities": [],
        "audit_provenance_summary": "Tampered"
    }

    mock_response = MagicMock()
    mock_response.text = json.dumps(tampered_payload)

    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        mock_client_cls.return_value = mock_client

        reasoner = LLMStructuredReasoner(api_key="mock-test-key-12345")
        narrative = reasoner.generate_narrative(sample_crisis_dossier)

        # Mathematical ground truth preserved
        assert narrative.composite_risk_index == 0.785
        assert narrative.risk_level == "CRITICAL"


def test_gemini_api_failure_falls_back(sample_crisis_dossier: InstitutionalDossier):
    """When Gemini API raises a network exception, reasoner falls back safely."""
    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = ConnectionError("Connection refused by host")
        mock_client_cls.return_value = mock_client

        reasoner = LLMStructuredReasoner(api_key="mock-test-key-12345")
        narrative = reasoner.generate_narrative(sample_crisis_dossier)

        # Safely received fallback output
        assert isinstance(narrative, ExecutiveNarrativeResponse)
        assert narrative.institution_id == "INST_CRISIS_01"
        assert narrative.composite_risk_index == 0.785
        assert "Placement Degradation" in narrative.executive_summary


def test_gemini_malformed_json_falls_back(sample_crisis_dossier: InstitutionalDossier):
    """When Gemini API returns non-JSON or malformed output, reasoner falls back safely."""
    mock_response = MagicMock()
    mock_response.text = "This is not valid JSON at all!"

    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        mock_client_cls.return_value = mock_client

        reasoner = LLMStructuredReasoner(api_key="mock-test-key-12345")
        narrative = reasoner.generate_narrative(sample_crisis_dossier)

        assert isinstance(narrative, ExecutiveNarrativeResponse)
        assert narrative.composite_risk_index == 0.785


def test_gemini_timeout_falls_back(sample_crisis_dossier: InstitutionalDossier):
    """When Gemini API times out, reasoner falls back safely."""
    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = TimeoutError("Request timed out after 30s")
        mock_client_cls.return_value = mock_client

        reasoner = LLMStructuredReasoner(api_key="mock-test-key-12345")
        narrative = reasoner.generate_narrative(sample_crisis_dossier)

        assert isinstance(narrative, ExecutiveNarrativeResponse)
        assert narrative.composite_risk_index == 0.785
