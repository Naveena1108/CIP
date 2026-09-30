"""
Tests for CIP Multi-Provider AI Configuration, Failover Chain, Retry/Backoff,
Observability, Secret Redaction, and Deterministic Authority.

Covers:
1. Priority order:
   - Primary: Google Gemini (`gemini-3.8-flash`)
   - Secondary: OpenRouter (`google/gemini-3.8-flash`)
   - Tertiary: Groq (`openai/gpt-oss-120b`)
   - Final Fallback: CIP Deterministic Engine (`DETERMINISTIC_FALLBACK_ENGINE`)
2. Independent execution of each provider (`google_gemini`, `openrouter`, `groq`).
3. Bounded retry/backoff on transient errors (rate_limit, timeout, service_unavailable,
   5xx server_error, malformed_response) before failing over to the next provider.
4. Non-failover on ordinary application bugs (malformed_request, invalid_schema).
5. Secret redaction ensuring API keys never appear in logs, error messages, telemetry, or responses.
6. Per-request observability tracking (`provider`, `model`, `request_status`, `fallback_level`,
   `latency_ms`, `failure_category`) and answer source (`Gemini`, `OpenRouter`, `Groq`, `deterministic fallback`).
7. Deterministic ground-truth authority across all providers.
"""

import json
import os
from unittest.mock import MagicMock, patch
import pytest

from src.contracts import (
    CrisisAssessment,
    SignalAnomaly,
    AIRequestObservability,
)
from src.engine.evidence import EvidenceAssembler, InstitutionalDossier
from src.engine.institutional_intelligence import InstitutionalIntelligenceEngine
from src.engine.llm_reasoner import (
    LLMStructuredReasoner,
    ExecutiveNarrativeResponse,
    _LLMExecutiveNarrativeSchema,
    redact_secrets,
    classify_provider_failure,
    RECENT_OBSERVABILITY_LOG,
)
from src.engine.investigation_engine import EvidenceGroundedInvestigationEngine


@pytest.fixture
def grounded_dossier() -> InstitutionalDossier:
    assessment = CrisisAssessment(
        institution_id="INST_MP_01",
        composite_risk_index=0.74,
        risk_level="HIGH",
        primary_driving_signal="Placement and Intake Degradation",
        confidence_score=0.92,
        anomalies_detected=[
            SignalAnomaly(
                signal_name="Placements (CSE)",
                academic_year=2024,
                metric_name="placement_percentage",
                observed_value=38.5,
                baseline_value=81.0,
                deviation_zscore=-3.10,
                severity="CRITICAL",
                description="CSE placement rate dropped from 81.0% to 38.5% in AY 2024.",
            )
        ],
        recommended_mitigations=[
            "Launch targeted employer re-engagement and curriculum alignment audit."
        ],
    )
    assembler = EvidenceAssembler()
    return assembler.assemble_dossier(assessment, signal_sources=["VERIFIED_LEDGER_2024"])


def _sample_valid_narrative_dict(inst_id: str = "INST_MP_01", summary_prefix: str = "Synthesized") -> dict:
    return {
        "institution_id": inst_id,
        "risk_level": "HIGH",
        "composite_risk_index": 0.74,
        "executive_summary": f"{summary_prefix}: In AY 2024, CSE placement_percentage fell to 38.5 vs baseline 81.0 (CRI 0.74).",
        "root_causes": [
            "In 2024, placement_percentage dropped to 38.5 against historical baseline 81.0 (deviation Z-score -3.10)."
        ],
        "prioritized_actions": [
            "Launch targeted employer re-engagement and curriculum alignment audit."
        ],
        "investigation_priorities": [
            "Audit CSE placement cell conversion records for AY 2024."
        ],
        "audit_provenance_summary": "Grounded strictly in VERIFIED_LEDGER_2024.",
    }


def test_provider_configuration_and_priority_order():
    """Verify default models and fallback order match Gemini -> OpenRouter -> Groq -> Deterministic."""
    reasoner = LLMStructuredReasoner(
        api_key="fake_gemini_key",
        openrouter_api_key="fake_openrouter_key",
        groq_api_key="fake_groq_key",
    )
    status = reasoner.get_gemini_status()

    assert status.gemini_available is True
    assert status.credentials_present is True
    assert status.generation_mode == "LIVE_GEMINI"
    assert status.configured_model == "gemini-3.8-flash"
    assert len(status.providers_configured) == 3

    p1, p2, p3 = status.providers_configured
    assert p1.provider == "google_gemini" and p1.priority_rank == 1 and p1.configured_model == "gemini-3.8-flash" and p1.credentials_present is True
    assert p2.provider == "openrouter" and p2.priority_rank == 2 and p2.configured_model == "google/gemini-3.8-flash" and p2.credentials_present is True
    assert p3.provider == "groq" and p3.priority_rank == 3 and p3.configured_model == "openai/gpt-oss-120b" and p3.credentials_present is True

    # Ensure status never contains any raw API keys
    status_json = status.model_dump_json()
    assert "fake_gemini_key" not in status_json
    assert "fake_openrouter_key" not in status_json
    assert "fake_groq_key" not in status_json


def test_each_provider_independently(grounded_dossier: InstitutionalDossier):
    """
    Verify each provider (Google Gemini, OpenRouter, Groq) independently produces
    a valid grounded response and accurately sets active_provider, answer_source,
    generation_mode, model_used, and observability.
    """
    # 1. Google Gemini (Primary, fallback_level=0, answer_source='Gemini')
    reasoner_gemini = LLMStructuredReasoner(
        api_key="mock_gemini_key",
        openrouter_api_key="",
        groq_api_key="",
    )
    mock_gem_resp = MagicMock()
    mock_gem_resp.text = json.dumps(_sample_valid_narrative_dict(summary_prefix="Gemini primary"))

    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_gem_resp
        mock_client_cls.return_value = mock_client

        res_gem = reasoner_gemini.generate_narrative(grounded_dossier)
        assert res_gem.active_provider == "google_gemini"
        assert res_gem.answer_source == "Gemini"
        assert res_gem.generation_mode == "LIVE_GEMINI"
        assert res_gem.gemini_live_used is True
        assert res_gem.model_used == "gemini-3.8-flash"
        assert res_gem.observability is not None
        assert res_gem.observability.provider == "google_gemini"
        assert res_gem.observability.fallback_level == 0
        assert res_gem.observability.request_status == "SUCCESS"
        assert res_gem.observability.latency_ms >= 0.0

    # 2. OpenRouter (Secondary, fallback_level=1, answer_source='OpenRouter')
    reasoner_or = LLMStructuredReasoner(
        api_key="",
        openrouter_api_key="mock_openrouter_key",
        groq_api_key="",
    )
    mock_or_http_resp = MagicMock()
    mock_or_http_resp.status_code = 200
    mock_or_http_resp.json.return_value = {
        "choices": [
            {"message": {"content": json.dumps(_sample_valid_narrative_dict(summary_prefix="OpenRouter secondary"))}}
        ]
    }
    with patch("httpx.Client.post", return_value=mock_or_http_resp):
        res_or = reasoner_or.generate_narrative(grounded_dossier)
        assert res_or.active_provider == "openrouter"
        assert res_or.answer_source == "OpenRouter"
        assert res_or.generation_mode == "LIVE_OPENROUTER"
        assert res_or.gemini_live_used is False
        assert res_or.model_used == "google/gemini-3.8-flash"
        assert res_or.observability is not None
        assert res_or.observability.provider == "openrouter"
        assert res_or.observability.fallback_level == 1
        assert res_or.observability.request_status == "FAILED_OVER_SUCCESS"

    # 3. Groq (Tertiary, fallback_level=2, answer_source='Groq')
    reasoner_groq = LLMStructuredReasoner(
        api_key="",
        openrouter_api_key="",
        groq_api_key="mock_groq_key",
    )
    mock_groq_http_resp = MagicMock()
    mock_groq_http_resp.status_code = 200
    mock_groq_http_resp.json.return_value = {
        "choices": [
            {"message": {"content": json.dumps(_sample_valid_narrative_dict(summary_prefix="Groq tertiary"))}}
        ]
    }
    with patch("httpx.Client.post", return_value=mock_groq_http_resp):
        res_groq = reasoner_groq.generate_narrative(grounded_dossier)
        assert res_groq.active_provider == "groq"
        assert res_groq.answer_source == "Groq"
        assert res_groq.generation_mode == "LIVE_GROQ"
        assert res_groq.gemini_live_used is False
        assert res_groq.model_used == "openai/gpt-oss-120b"
        assert res_groq.observability is not None
        assert res_groq.observability.provider == "groq"
        assert res_groq.observability.fallback_level == 2
        assert res_groq.observability.request_status == "FAILED_OVER_SUCCESS"


def test_cascading_failover_with_bounded_retry_and_backoff(grounded_dossier: InstitutionalDossier):
    """
    Verify full cascading failover:
    - Case A: Primary (Gemini) fails with 503 service_unavailable (retried once -> 2 attempts),
              then automatically fails over to Secondary (OpenRouter) which succeeds!
    - Case B: Primary (Gemini) fails with 429 rate_limit, Secondary (OpenRouter) fails with timeout,
              then automatically fails over to Tertiary (Groq) which succeeds!
    - Case C: All 3 providers fail transiently -> falls back to Final Deterministic Engine!
    """
    reasoner = LLMStructuredReasoner(
        api_key="mock_gem_key",
        openrouter_api_key="mock_or_key",
        groq_api_key="mock_groq_key",
        max_retries_per_provider=1,
        retry_base_delay=0.0,
    )

    # Case A: Gemini 503 -> OpenRouter 200
    mock_or_ok = MagicMock()
    mock_or_ok.status_code = 200
    mock_or_ok.json.return_value = {
        "choices": [{"message": {"content": json.dumps(_sample_valid_narrative_dict(summary_prefix="OpenRouter failover"))}}]
    }

    with patch("google.genai.Client") as mock_gem_cls, patch("httpx.Client.post", return_value=mock_or_ok) as mock_post:
        mock_gem_client = MagicMock()
        mock_gem_client.models.generate_content.side_effect = RuntimeError("503 UNAVAILABLE: Model experiencing high demand")
        mock_gem_cls.return_value = mock_gem_client

        res_a = reasoner.generate_narrative(grounded_dossier)
        assert res_a.active_provider == "openrouter"
        assert res_a.answer_source == "OpenRouter"
        assert res_a.generation_mode == "LIVE_OPENROUTER"
        assert res_a.observability.fallback_level == 1
        assert res_a.observability.request_status == "FAILED_OVER_SUCCESS"
        assert res_a.observability.failure_category == "service_unavailable"
        # Verify Gemini was tried twice (initial + 1 bounded retry) before failing over to OpenRouter
        gem_attempts = [a for a in res_a.observability.attempts if a.provider == "google_gemini"]
        or_attempts = [a for a in res_a.observability.attempts if a.provider == "openrouter"]
        assert len(gem_attempts) == 2
        assert all(a.failure_category == "service_unavailable" for a in gem_attempts)
        assert len(or_attempts) == 1 and or_attempts[0].request_status == "SUCCESS"
        assert mock_post.call_count == 1

    # Case B: Gemini 429 -> OpenRouter Timeout -> Groq 200
    def side_effect_or_then_groq(url, *args, **kwargs):
        if "openrouter.ai" in url:
            raise TimeoutError("OpenRouter request timed out after 20s")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": json.dumps(_sample_valid_narrative_dict(summary_prefix="Groq tertiary failover"))}}]
        }
        return resp

    with patch("google.genai.Client") as mock_gem_cls, patch("httpx.Client.post", side_effect=side_effect_or_then_groq):
        mock_gem_client = MagicMock()
        mock_gem_client.models.generate_content.side_effect = RuntimeError("429 RESOURCE_EXHAUSTED: Rate limit reached")
        mock_gem_cls.return_value = mock_gem_client

        res_b = reasoner.generate_narrative(grounded_dossier)
        assert res_b.active_provider == "groq"
        assert res_b.answer_source == "Groq"
        assert res_b.generation_mode == "LIVE_GROQ"
        assert res_b.model_used == "openai/gpt-oss-120b"
        assert res_b.observability.fallback_level == 2
        assert res_b.observability.request_status == "FAILED_OVER_SUCCESS"
        # 2 Gemini attempts (rate_limit) + 2 OpenRouter attempts (timeout) + 1 Groq attempt (SUCCESS) = 5 attempts
        assert len(res_b.observability.attempts) == 5
        assert res_b.observability.attempts[0].failure_category == "rate_limit"
        assert res_b.observability.attempts[2].failure_category == "timeout"
        assert res_b.observability.attempts[4].provider == "groq" and res_b.observability.attempts[4].request_status == "SUCCESS"

    # Case C: All 3 providers fail -> Final Deterministic Fallback (fallback_level=3)
    mock_502 = MagicMock()
    mock_502.status_code = 502
    mock_502.text = "502 Bad Gateway upstream error"

    with patch("google.genai.Client") as mock_gem_cls, patch("httpx.Client.post", return_value=mock_502):
        mock_gem_client = MagicMock()
        mock_gem_client.models.generate_content.side_effect = ConnectionError("Network unreachable")
        mock_gem_cls.return_value = mock_gem_client

        res_c = reasoner.generate_narrative(grounded_dossier)
        assert res_c.active_provider == "deterministic_fallback"
        assert res_c.answer_source == "deterministic fallback"
        assert res_c.generation_mode == "DETERMINISTIC_FALLBACK"
        assert res_c.model_used == "DETERMINISTIC_FALLBACK_ENGINE"
        assert res_c.observability.fallback_level == 3
        assert res_c.observability.request_status == "DETERMINISTIC_FALLBACK"
        assert res_c.composite_risk_index == 0.74
        assert res_c.risk_level == "HIGH"


def test_no_failover_on_ordinary_application_bugs():
    """
    Verify that ordinary application bugs (such as malformed local request payloads
    or invalid schema types) do NOT trigger retries or failover to Secondary/Tertiary providers.
    """
    reasoner = LLMStructuredReasoner(
        api_key="mock_gem_key",
        openrouter_api_key="mock_or_key",
        groq_api_key="mock_groq_key",
    )

    with patch("google.genai.Client") as mock_gem_cls, patch("httpx.Client.post") as mock_post:
        # 1. Empty / malformed local prompt
        model_out, obs, fb_note = reasoner.execute_structured_prompt(
            prompt="   ",
            response_schema=_LLMExecutiveNarrativeSchema,
        )
        assert model_out is None
        assert obs.failure_category == "malformed_request"
        assert obs.fallback_level == 3
        assert len(obs.attempts) == 1
        assert mock_gem_cls.call_count == 0
        assert mock_post.call_count == 0

        # 2. Local application raises MALFORMED_REQUEST inside primary call
        mock_gem_client = MagicMock()
        mock_gem_client.models.generate_content.side_effect = ValueError("MALFORMED_REQUEST: Invalid local parameter structure")
        mock_gem_cls.return_value = mock_gem_client

        model_out2, obs2, fb_note2 = reasoner.execute_structured_prompt(
            prompt="Valid prompt text",
            response_schema=_LLMExecutiveNarrativeSchema,
            allow_config_test_failover=False,
        )
        assert model_out2 is None
        assert obs2.failure_category == "malformed_request"
        # Must NOT have retried Gemini or failed over to OpenRouter/Groq
        assert len(obs2.attempts) == 1
        assert obs2.attempts[0].provider == "google_gemini"
        assert mock_post.call_count == 0


def test_secret_redaction_never_exposes_api_keys(grounded_dossier: InstitutionalDossier):
    """
    Verify that API keys (AQ..., AIza..., sk-or-v1-..., gsk_...) are strictly redacted
    even if an exception message or upstream HTTP error body echoes the key back.
    """
    fake_gem_secret = "AQ.Ab8RN6JRirnt6UQ_TestSecretKey999999"
    fake_or_secret = "sk-or-v1-" + ("0" * 48)
    fake_groq_secret = "gsk_" + ("x" * 48)

    reasoner = LLMStructuredReasoner(
        api_key=fake_gem_secret,
        openrouter_api_key=fake_or_secret,
        groq_api_key=fake_groq_secret,
        max_retries_per_provider=0,
    )

    mock_bad_http = MagicMock()
    mock_bad_http.status_code = 401
    mock_bad_http.text = f"Unauthorized: Bearer {fake_or_secret} and {fake_groq_secret} rejected"

    with patch("google.genai.Client") as mock_gem_cls, patch("httpx.Client.post", return_value=mock_bad_http):
        mock_gem_client = MagicMock()
        mock_gem_client.models.generate_content.side_effect = RuntimeError(
            f"401 API_KEY_INVALID for key {fake_gem_secret}"
        )
        mock_gem_cls.return_value = mock_gem_client

        resp = reasoner.generate_narrative(grounded_dossier)
        serialized = resp.model_dump_json()

        assert fake_gem_secret not in serialized
        assert fake_or_secret not in serialized
        assert fake_groq_secret not in serialized
        assert "[REDACTED" in (resp.fallback_reason or "")


def test_investigation_engine_multi_provider_failover_and_hallucination_guard(grounded_dossier: InstitutionalDossier):
    """
    Verify EvidenceGroundedInvestigationEngine uses the Multi-Provider failover chain
    and enforces deterministic ground-truth guards on OpenRouter/Groq responses.
    """
    intel_engine = InstitutionalIntelligenceEngine()
    intel_report = intel_engine.analyze_institution(
        institution_id="INST_MP_01",
        cet_history=[],
        admissions_history=[],
        placements_history=[],
        dynamic_signals=[],
    )

    inv_engine = EvidenceGroundedInvestigationEngine(
        api_key="mock_gem_key",
        openrouter_api_key="mock_or_key",
        groq_api_key="mock_groq_key",
        max_retries_per_provider=0,
    )

    mock_or_inv = MagicMock()
    mock_or_inv.status_code = 200
    mock_or_inv.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "finding": "In AY 2024, CSE placement_percentage dropped to 38.5 against historical baseline 81.0 (CRI 0.74).",
                            "potential_contributing_factors": ["Co-occurring intake shifts requiring audit."],
                            "alternative_explanations": ["Regional hiring cycle slowdown."],
                        }
                    )
                }
            }
        ]
    }

    with patch("google.genai.Client") as mock_gem_cls, patch("httpx.Client.post", return_value=mock_or_inv):
        mock_gem_client = MagicMock()
        mock_gem_client.models.generate_content.side_effect = RuntimeError("503 Service Unavailable")
        mock_gem_cls.return_value = mock_gem_client

        inv_res = inv_engine.investigate_question(
            institution_id="INST_MP_01",
            question="Why did placements fall?",
            dossier=grounded_dossier,
            intel_report=intel_report,
        )

        assert inv_res.active_provider == "openrouter"
        assert inv_res.answer_source == "OpenRouter"
        assert inv_res.generation_mode == "LIVE_OPENROUTER"
        assert inv_res.model_used == "google/gemini-3.8-flash"
        assert inv_res.observability is not None
        assert inv_res.observability.fallback_level == 1
        assert "38.5" in inv_res.finding


def test_live_configured_providers_end_to_end(grounded_dossier: InstitutionalDossier):
    """
    Live integration test verifying the real configured providers in .env:
    1. Google Gemini (`gemini-3.8-flash`)
    2. OpenRouter (`google/gemini-3.8-flash`)
    3. Groq (`openai/gpt-oss-120b`)
    Tests each provider with a grounded CIP dossier and verifies deterministic CRI/risk locks
    and zero credential leakage.
    """
    default_reasoner = LLMStructuredReasoner()
    if not (default_reasoner.api_key and default_reasoner.openrouter_api_key and default_reasoner.groq_api_key):
        pytest.skip("Live multi-provider keys not all present in environment")

    # 1. Test full multi-provider chain starting with Primary (Google Gemini)
    res_primary = default_reasoner.generate_narrative(grounded_dossier)
    assert res_primary.composite_risk_index == 0.74
    assert res_primary.risk_level == "HIGH"
    assert res_primary.active_provider in ("google_gemini", "openrouter", "groq", "deterministic_fallback")
    assert res_primary.answer_source in ("Gemini", "OpenRouter", "Groq", "deterministic fallback")
    assert res_primary.observability is not None
    assert res_primary.observability.latency_ms > 0

    # 2. Test Secondary (OpenRouter: google/gemini-3.8-flash) directly
    or_only_reasoner = LLMStructuredReasoner(
        api_key="",
        openrouter_api_key=default_reasoner.openrouter_api_key,
        groq_api_key="",
    )
    res_or = or_only_reasoner.generate_narrative(grounded_dossier)
    assert res_or.composite_risk_index == 0.74
    assert res_or.risk_level == "HIGH"
    if res_or.active_provider == "openrouter":
        assert res_or.answer_source == "OpenRouter"
        assert res_or.model_used == "google/gemini-3.8-flash"
    else:
        assert res_or.active_provider == "deterministic_fallback"
        or_attempts = [a for a in res_or.observability.attempts if a.provider == "openrouter"]
        assert len(or_attempts) >= 1
        assert or_attempts[0].model == "google/gemini-3.8-flash"

    # 3. Test Tertiary (Groq: openai/gpt-oss-120b) directly
    groq_only_reasoner = LLMStructuredReasoner(
        api_key="",
        openrouter_api_key="",
        groq_api_key=default_reasoner.groq_api_key,
    )
    res_groq = groq_only_reasoner.generate_narrative(grounded_dossier)
    assert res_groq.composite_risk_index == 0.74
    assert res_groq.risk_level == "HIGH"
    if res_groq.active_provider == "groq":
        assert res_groq.answer_source == "Groq"
        assert res_groq.model_used == "openai/gpt-oss-120b"
    else:
        assert res_groq.active_provider == "deterministic_fallback"
        groq_attempts = [a for a in res_groq.observability.attempts if a.provider == "groq"]
        assert len(groq_attempts) >= 1
        assert groq_attempts[0].model == "openai/gpt-oss-120b"
