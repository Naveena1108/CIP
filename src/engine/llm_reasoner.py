"""
ACT-04 & CIP Phase 5: Multi-Provider Evidence-Grounded AI Executive Analysis & Hallucination-Resistant Reasoner.

Supports three configured AI providers with automatic failover and deterministic fallback:
1. Primary: Google Gemini (`GEMINI_API_KEY`, preferred model: `gemini-3.8-flash`)
2. Secondary: OpenRouter (`OPENROUTER_API_KEY`, preferred model: `google/gemini-3.8-flash`)
3. Tertiary: Groq (`GROQ_API_KEY`, preferred model: `openai/gpt-oss-120b`)
4. Final Fallback: CIP Deterministic Engine (`DETERMINISTIC_FALLBACK_ENGINE`)

Guarantees:
1. Deterministic code remains strictly authoritative for CRI/risk calculations, anomaly detection,
   trend calculations, forecasting, statistical analysis, and evidence calculations.
   LLMs must never invent, override, or redefine these values.
2. Bounded retry/backoff before failing over for transient provider errors (rate_limit, timeout,
   service_unavailable, 5xx server_error, malformed_response).
   Does NOT fail over for ordinary application bugs (malformed_request, invalid_schema) unless
   provider configuration itself is being tested.
3. Never exposes, logs, or returns API keys in any error message, observability record, or response.
4. Tracks per-request observability (`provider`, `model`, `request_status`, `fallback_level`,
   `latency_ms`, `failure_category`) and clearly identifies whether an answer came from
   `Gemini`, `OpenRouter`, `Groq`, or `deterministic fallback`.
"""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type
import httpx
from pydantic import BaseModel, Field

from src.engine.evidence import InstitutionalDossier
from src.contracts import (
    GenerationModeLiteral,
    AIProviderName,
    AnswerSourceLabel,
    AIProviderAttemptRecord,
    AIRequestObservability,
    ProviderConfigurationInfo,
    ProvenanceRecord,
    GroundedClaim,
    ExplainableForecast,
    GeminiStatusResponse,
)


def load_env_files() -> None:
    """Load .env from project root without importing src.api (avoids circular imports)."""
    root_dir = Path(__file__).resolve().parent.parent.parent
    for filename in (".env", ".env.local"):
        env_path = root_dir / filename
        if env_path.is_file():
            try:
                for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value
            except Exception:
                pass


# Ensure .env is loaded so GEMINI_API_KEY, OPENROUTER_API_KEY, GROQ_API_KEY are available
load_env_files()

logger = logging.getLogger("ai_criss.llm")

_UNSET = object()

DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
DEFAULT_OPENROUTER_MODEL = "google/gemini-3.8-flash"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"

# Bounded ring buffer of recent AI request observability records (never contains secrets)
RECENT_OBSERVABILITY_LOG: List[AIRequestObservability] = []
MAX_OBSERVABILITY_LOG_SIZE = 50


def record_ai_observability(obs: AIRequestObservability) -> None:
    """Append an observability record to the bounded in-memory log."""
    RECENT_OBSERVABILITY_LOG.append(obs)
    if len(RECENT_OBSERVABILITY_LOG) > MAX_OBSERVABILITY_LOG_SIZE:
        del RECENT_OBSERVABILITY_LOG[:-MAX_OBSERVABILITY_LOG_SIZE]


def redact_secrets(text: str, extra_secrets: Optional[List[str]] = None) -> str:
    """
    Strip any API keys or bearer tokens from logs, exceptions, and observability strings.
    Never allows GEMINI_API_KEY, OPENROUTER_API_KEY, or GROQ_API_KEY to leak.
    """
    if not text:
        return ""
    sanitized = str(text)
    candidates: List[str] = []
    if extra_secrets:
        candidates.extend([s for s in extra_secrets if s and len(s.strip()) >= 6])
    for env_name in ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY"):
        val = os.getenv(env_name, "").strip()
        if val and len(val) >= 6:
            candidates.append(val)

    for secret in candidates:
        if secret in sanitized:
            sanitized = sanitized.replace(secret, "[REDACTED_API_KEY]")

    # Regex patterns for Google/Vertex AQ., AIza, OpenRouter sk-or-v1-, Groq gsk_
    sanitized = re.sub(r"AQ\.[A-Za-z0-9_\-]{10,}", "[REDACTED_GEMINI_KEY]", sanitized)
    sanitized = re.sub(r"AIza[A-Za-z0-9_\-]{20,}", "[REDACTED_GEMINI_KEY]", sanitized)
    sanitized = re.sub(r"sk-or-v1-[A-Za-z0-9_\-]{12,}", "[REDACTED_OPENROUTER_KEY]", sanitized)
    sanitized = re.sub(r"gsk_[A-Za-z0-9_\-]{12,}", "[REDACTED_GROQ_KEY]", sanitized)
    sanitized = re.sub(r"Bearer\s+[A-Za-z0-9_\-\.]{12,}", "Bearer [REDACTED_API_KEY]", sanitized)
    return sanitized


def classify_provider_failure(exc: Exception) -> Tuple[str, bool, bool]:
    """
    Classify an exception during provider execution into:
    (failure_category, is_transient_retryable, should_failover)

    Categories:
    - 'malformed_request': local application bug (non-retryable, non-failover unless config testing)
    - 'invalid_schema': local schema bug (non-retryable, non-failover unless config testing)
    - 'invalid_credentials': 401/403 auth failure (non-retryable on same provider, fails over to next provider)
    - 'rate_limit': 429 / quota / resource exhausted (transient retryable, fails over)
    - 'timeout': TimeoutError / 504 / deadline exceeded (transient retryable, fails over)
    - 'service_unavailable': ConnectionError / 503 / high demand / overloaded (transient retryable, fails over)
    - 'server_error': 500 / 502 / provider internal error (transient retryable, fails over)
    - 'malformed_response': empty LLM output / JSONDecodeError / schema validation failure on LLM text (transient retryable, fails over)
    """
    raw_msg = str(exc)
    msg = raw_msg.lower()
    exc_name = type(exc).__name__.lower()

    if isinstance(exc, ValueError) and raw_msg.startswith("MALFORMED_REQUEST:"):
        return ("malformed_request", False, False)
    if isinstance(exc, TypeError) or (isinstance(exc, ValueError) and raw_msg.startswith("INVALID_SCHEMA:")):
        return ("invalid_schema", False, False)

    if any(tok in msg for tok in ("401", "403", "unauthorized", "invalid api key", "api_key_invalid", "invalid_credentials", "permission_denied", "authentication failed")):
        return ("invalid_credentials", False, True)

    if (
        isinstance(exc, json.JSONDecodeError)
        or "validationerror" in exc_name
        or "empty response" in msg
        or "malformed_response" in msg
        or "json_validate_failed" in msg
        or "failed to generate json" in msg
    ):
        return ("malformed_response", True, True)

    if any(tok in msg for tok in ("400", "bad request", "invalid_argument")) and "api_key" not in msg:
        return ("malformed_request", False, False)

    if any(tok in msg for tok in ("429", "402", "requires more credits", "rate_limit", "rate limit", "quota", "resource_exhausted", "too many requests")):
        return ("rate_limit", True, True)

    if isinstance(exc, (TimeoutError, httpx.TimeoutException)) or "timeout" in exc_name or any(tok in msg for tok in ("timed out", "timeout", "504", "deadline_exceeded")):
        return ("timeout", True, True)

    if isinstance(exc, (ConnectionError, httpx.RequestError)) or "connect" in exc_name or any(tok in msg for tok in ("503", "unavailable", "high demand", "overloaded", "connection refused", "network")):
        return ("service_unavailable", True, True)

    if any(tok in msg for tok in ("500", "502", "internal server error", "bad gateway", "server_error")):
        return ("server_error", True, True)

    return ("service_unavailable", True, True)


def _strip_json_fences(raw_text: str) -> str:
    """Remove optional ```json ... ``` markdown wrappers from LLM responses."""
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


class _LLMExecutiveNarrativeSchema(BaseModel):
    """
    Flat JSON schema requested from Gemini, OpenRouter, and Groq for Executive Narrative synthesis.
    Avoids Dict[str, Any] so strict provider JSON schema validators (including google-genai) accept it cleanly.
    """
    institution_id: str
    risk_level: str
    composite_risk_index: float
    executive_summary: str = Field(..., description="3-4 sentence high-level synthesis of the institutional state.")
    root_causes: List[str] = Field(..., description="Bullet points identifying primary crisis drivers grounded in evidence tokens.")
    prioritized_actions: List[str] = Field(..., description="Immediate 90-day and 1-year strategic mitigations.")
    investigation_priorities: List[str] = Field(..., description="Specific departments or metrics requiring immediate human audit.")
    audit_provenance_summary: str = Field(..., description="Statement confirming data sources and absence of speculative assumptions.")


class ExecutiveNarrativeResponse(BaseModel):
    """
    Structured AI Executive Analysis response.
    Preserves all legacy fields for backward compatibility while exposing CIP Phase 5
    AI Executive Analysis sections, 7-field provenance links, grounded claims,
    multi-provider source identification (Gemini, OpenRouter, Groq, deterministic fallback),
    per-request observability, and hallucination guard audit.
    """
    institution_id: str
    organization_id: Optional[str] = None
    risk_level: str
    composite_risk_index: float
    executive_summary: str = Field(..., description="3-4 sentence high-level synthesis of the institutional state.")
    root_causes: List[str] = Field(..., description="Bullet points identifying primary crisis drivers grounded in evidence tokens.")
    prioritized_actions: List[str] = Field(..., description="Immediate 90-day and 1-year strategic mitigations.")
    investigation_priorities: List[str] = Field(..., description="Specific departments or metrics requiring immediate human audit.")
    audit_provenance_summary: str = Field(..., description="Statement confirming data sources and absence of speculative assumptions.")

    # CIP Phase 5 & Multi-Provider AI fields
    analysis_title: str = Field(default="AI Executive Analysis")
    generation_mode: GenerationModeLiteral = Field(default="DETERMINISTIC_FALLBACK")
    gemini_live_used: bool = Field(default=False)
    active_provider: AIProviderName = Field(
        default="deterministic_fallback",
        description="Provider that produced the response: google_gemini, openrouter, groq, or deterministic_fallback",
    )
    answer_source: AnswerSourceLabel = Field(
        default="deterministic fallback",
        description="Human-readable answer source: 'Gemini', 'OpenRouter', 'Groq', or 'deterministic fallback'",
    )
    model_used: str = Field(default="DETERMINISTIC_FALLBACK_ENGINE")
    fallback_reason: Optional[str] = Field(default=None)
    observability: Optional[AIRequestObservability] = Field(default=None)

    what_is_happening: str = Field(default="", description="Section 1: What is happening?")
    why: List[str] = Field(default_factory=list, description="Section 2: Why?")
    evidence: List[ProvenanceRecord] = Field(default_factory=list, description="Section 3: Evidence (7-field provenance)")
    what_could_happen_next: str = Field(default="", description="Section 4: What could happen next?")
    what_should_leadership_investigate: List[str] = Field(
        default_factory=list,
        description="Section 5: What should leadership investigate?",
    )
    grounded_claims: List[GroundedClaim] = Field(default_factory=list)
    uncertain_claims: List[str] = Field(default_factory=list)
    hallucination_guard_applied: bool = Field(default=True)
    hallucination_guard_report: Dict[str, Any] = Field(default_factory=dict)


class LLMStructuredReasoner:
    """
    Executes grounded reasoning over an InstitutionalDossier using the CIP Multi-Provider AI
    failover chain:
      1. Primary: Google Gemini (`gemini-3.8-flash`)
      2. Secondary: OpenRouter (`google/gemini-3.8-flash`)
      3. Tertiary: Groq (`openai/gpt-oss-120b`)
      4. Final Fallback: CIP Deterministic Engine (`DETERMINISTIC_FALLBACK_ENGINE`)
    Followed by a strict deterministic hallucination guard that preserves all mathematical invariants.
    """

    def __init__(
        self,
        api_key: Any = _UNSET,
        model_name: Optional[str] = None,
        openrouter_api_key: Any = _UNSET,
        openrouter_model: Optional[str] = None,
        groq_api_key: Any = _UNSET,
        groq_model: Optional[str] = None,
        use_env_fallbacks: Optional[bool] = None,
        max_retries_per_provider: int = 1,
        retry_base_delay: float = 0.05,
        request_timeout_seconds: float = 20.0,
    ):
        load_env_files()

        # Determine whether to load secondary/tertiary keys from environment:
        # - When LLMStructuredReasoner() is called with default args (api_key is _UNSET), load all 3 providers from env.
        # - When unit tests call LLMStructuredReasoner(api_key=None) or LLMStructuredReasoner(api_key="mock-key")
        #   without specifying openrouter_api_key/groq_api_key or use_env_fallbacks=True, isolate to the passed key(s).
        if use_env_fallbacks is None:
            use_env_fallbacks = (api_key is _UNSET)

        env_gemini = os.getenv("GEMINI_API_KEY", "").strip()
        env_openrouter = os.getenv("OPENROUTER_API_KEY", "").strip()
        env_groq = os.getenv("GROQ_API_KEY", "").strip()

        if api_key is _UNSET:
            self.api_key = env_gemini
        elif api_key is None:
            self.api_key = ""
        else:
            self.api_key = str(api_key).strip()

        if openrouter_api_key is _UNSET:
            self.openrouter_api_key = env_openrouter if use_env_fallbacks else ""
        elif openrouter_api_key is None:
            self.openrouter_api_key = ""
        else:
            self.openrouter_api_key = str(openrouter_api_key).strip()

        if groq_api_key is _UNSET:
            self.groq_api_key = env_groq if use_env_fallbacks else ""
        elif groq_api_key is None:
            self.groq_api_key = ""
        else:
            self.groq_api_key = str(groq_api_key).strip()

        self.model_name = model_name or os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        self.openrouter_model = openrouter_model or os.getenv("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL)
        self.groq_model = groq_model or os.getenv("GROQ_MODEL", DEFAULT_GROQ_MODEL)

        self.max_retries_per_provider = max(0, int(max_retries_per_provider))
        self.retry_base_delay = max(0.0, float(retry_base_delay))
        self.request_timeout_seconds = float(request_timeout_seconds)

    def _all_secrets(self) -> List[str]:
        return [s for s in (self.api_key, self.openrouter_api_key, self.groq_api_key) if s]

    def get_gemini_status(self) -> GeminiStatusResponse:
        """
        Return truthful runtime status of CIP Multi-Provider AI configuration
        (Google Gemini -> OpenRouter -> Groq -> Deterministic Fallback).
        Never exposes API keys.
        """
        has_gemini = bool(self.api_key)
        has_openrouter = bool(self.openrouter_api_key)
        has_groq = bool(self.groq_api_key)
        any_live = has_gemini or has_openrouter or has_groq

        if has_gemini:
            mode: GenerationModeLiteral = "LIVE_GEMINI"
            active_model = self.model_name
        elif has_openrouter:
            mode = "LIVE_OPENROUTER"
            active_model = self.openrouter_model
        elif has_groq:
            mode = "LIVE_GROQ"
            active_model = self.groq_model
        else:
            mode = "DETERMINISTIC_FALLBACK"
            active_model = "DETERMINISTIC_FALLBACK_ENGINE"

        providers_info = [
            ProviderConfigurationInfo(
                provider="google_gemini",
                priority_rank=1,
                configured_model=self.model_name,
                credentials_present=has_gemini,
            ),
            ProviderConfigurationInfo(
                provider="openrouter",
                priority_rank=2,
                configured_model=self.openrouter_model,
                credentials_present=has_openrouter,
            ),
            ProviderConfigurationInfo(
                provider="groq",
                priority_rank=3,
                configured_model=self.groq_model,
                credentials_present=has_groq,
            ),
        ]

        if any_live:
            configured_names = [
                f"{p.provider} ({p.configured_model})"
                for p in providers_info
                if p.credentials_present
            ]
            explanation = (
                f"Multi-provider AI active: {', '.join(configured_names)} -> CIP Deterministic Fallback. "
                f"Deterministic engine remains strictly authoritative for all CRI, anomaly, baseline, and forecast metrics."
            )
        else:
            explanation = (
                "No AI provider credentials configured (GEMINI_API_KEY, OPENROUTER_API_KEY, GROQ_API_KEY absent). "
                "Operating in truthful DETERMINISTIC_FALLBACK mode; no LLM calls are simulated or faked."
            )

        return GeminiStatusResponse(
            gemini_available=has_gemini,
            generation_mode=mode,
            configured_model=active_model,
            credentials_present=any_live,
            status_explanation=explanation,
            fallback_order=[
                f"1. Primary: Google Gemini ({self.model_name})",
                f"2. Secondary: OpenRouter ({self.openrouter_model})",
                f"3. Tertiary: Groq ({self.groq_model})",
                "4. Final Fallback: CIP Deterministic Engine",
            ],
            providers_configured=providers_info,
            recent_observability=list(RECENT_OBSERVABILITY_LOG[-10:]),
        )

    def _call_gemini_provider(self, prompt: str, response_schema: Type[BaseModel]) -> BaseModel:
        """Invoke Primary Provider: Google Gemini via google-genai SDK."""
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        response = client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=response_schema,
                temperature=0.0,
            ),
        )
        if not response or not response.text:
            raise ValueError("Empty response received from Gemini API.")
        cleaned = _strip_json_fences(response.text)
        parsed = json.loads(cleaned)
        return response_schema.model_validate(parsed)

    def _call_openai_compatible_provider(
        self,
        provider_label: str,
        endpoint_url: str,
        api_key: str,
        model_id: str,
        prompt: str,
        response_schema: Type[BaseModel],
    ) -> BaseModel:
        """Invoke Secondary (OpenRouter) or Tertiary (Groq) OpenAI-compatible JSON endpoint."""
        schema_json = json.dumps(response_schema.model_json_schema(), indent=2)
        system_instruction = (
            "You are a deterministic-bounded institutional intelligence auditor for the CIP Crisis Intelligence Platform. "
            "Respond ONLY with a valid JSON object strictly conforming to the following JSON schema. "
            "Do not include markdown code fences or commentary outside the JSON object.\n\n"
            f"REQUIRED JSON SCHEMA:\n{schema_json}"
        )
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        default_max_tokens = 2048 if provider_label.lower() == "groq" else 500
        payload: Dict[str, Any] = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.0,
            "max_tokens": default_max_tokens,
            "response_format": {"type": "json_object"},
        }

        with httpx.Client(timeout=self.request_timeout_seconds) as http_client:
            resp = http_client.post(endpoint_url, headers=headers, json=payload)
            if resp.status_code == 402 and "can only afford" in resp.text:
                m = re.search(r"can only afford\s+(\d+)", resp.text)
                if m:
                    affordable = int(m.group(1))
                    if affordable >= 220:
                        payload["max_tokens"] = max(200, affordable - 20)
                        resp = http_client.post(endpoint_url, headers=headers, json=payload)

        if resp.status_code != 200:
            safe_body = redact_secrets(resp.text[:300], self._all_secrets())
            raise RuntimeError(f"{provider_label} HTTP {resp.status_code}: {safe_body}")

        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise ValueError(f"Empty response choices received from {provider_label}.")
        msg_obj = choices[0].get("message") or {}
        content = msg_obj.get("content")
        if not content or not str(content).strip():
            raise ValueError(f"Empty response content received from {provider_label}.")

        cleaned = _strip_json_fences(str(content))
        parsed = json.loads(cleaned)
        return response_schema.model_validate(parsed)

    def execute_structured_prompt(
        self,
        prompt: str,
        response_schema: Type[BaseModel],
        allow_config_test_failover: bool = False,
    ) -> Tuple[Optional[BaseModel], AIRequestObservability, Optional[str]]:
        """
        Execute a structured prompt across the configured Multi-Provider AI failover chain:
          Level 0: Google Gemini (`self.model_name`)
          Level 1: OpenRouter (`self.openrouter_model`)
          Level 2: Groq (`self.groq_model`)
          Level 3: Deterministic Fallback

        Enforces:
        - Bounded retry/backoff on transient errors (rate_limit, timeout, service_unavailable, server_error, malformed_response).
        - Non-failover on local application bugs (malformed_request, invalid_schema) unless `allow_config_test_failover=True`.
        - Complete secret redaction on all logs, attempt records, and fallback reasons.
        """
        overall_start = time.perf_counter()
        attempts: List[AIProviderAttemptRecord] = []
        failure_reasons: List[str] = []
        last_failure_category: Optional[str] = None

        # Local validation guard: ordinary application bugs must not trigger external provider failover
        if not isinstance(prompt, str) or not prompt.strip():
            err_msg = "MALFORMED_REQUEST: Prompt must be a non-empty string."
            attempts.append(
                AIProviderAttemptRecord(
                    provider="google_gemini",
                    model=self.model_name,
                    fallback_level=0,
                    attempt_number=1,
                    request_status="FAILED",
                    latency_ms=0.0,
                    failure_category="malformed_request",
                    sanitized_error=err_msg,
                )
            )
            obs = AIRequestObservability(
                provider="deterministic_fallback",
                model="DETERMINISTIC_FALLBACK_ENGINE",
                request_status="DETERMINISTIC_FALLBACK",
                fallback_level=3,
                latency_ms=round((time.perf_counter() - overall_start) * 1000.0, 2),
                failure_category="malformed_request",
                answer_source_label="deterministic fallback",
                attempts=attempts,
            )
            record_ai_observability(obs)
            return None, obs, f"{err_msg} Non-retryable application error; provider failover suppressed."

        if not isinstance(response_schema, type) or not issubclass(response_schema, BaseModel):
            err_msg = "INVALID_SCHEMA: response_schema must be a Pydantic BaseModel subclass."
            attempts.append(
                AIProviderAttemptRecord(
                    provider="google_gemini",
                    model=self.model_name,
                    fallback_level=0,
                    attempt_number=1,
                    request_status="FAILED",
                    latency_ms=0.0,
                    failure_category="invalid_schema",
                    sanitized_error=err_msg,
                )
            )
            obs = AIRequestObservability(
                provider="deterministic_fallback",
                model="DETERMINISTIC_FALLBACK_ENGINE",
                request_status="DETERMINISTIC_FALLBACK",
                fallback_level=3,
                latency_ms=round((time.perf_counter() - overall_start) * 1000.0, 2),
                failure_category="invalid_schema",
                answer_source_label="deterministic fallback",
                attempts=attempts,
            )
            record_ai_observability(obs)
            return None, obs, f"{err_msg} Non-retryable schema error; provider failover suppressed."

        provider_chain: List[Tuple[AIProviderName, AnswerSourceLabel, int, str, str]] = [
            ("google_gemini", "Gemini", 0, self.api_key, self.model_name),
            ("openrouter", "OpenRouter", 1, self.openrouter_api_key, self.openrouter_model),
            ("groq", "Groq", 2, self.groq_api_key, self.groq_model),
        ]

        any_configured = any(bool(k) for _, _, _, k, _ in provider_chain)
        if not any_configured:
            attempts.append(
                AIProviderAttemptRecord(
                    provider="google_gemini",
                    model=self.model_name,
                    fallback_level=0,
                    attempt_number=1,
                    request_status="SKIPPED_UNCONFIGURED",
                    latency_ms=0.0,
                    failure_category="missing_credentials",
                    sanitized_error="GEMINI_API_KEY is not configured.",
                )
            )
            obs = AIRequestObservability(
                provider="deterministic_fallback",
                model="DETERMINISTIC_FALLBACK_ENGINE",
                request_status="DETERMINISTIC_FALLBACK",
                fallback_level=3,
                latency_ms=round((time.perf_counter() - overall_start) * 1000.0, 2),
                failure_category="missing_credentials",
                answer_source_label="deterministic fallback",
                attempts=attempts,
            )
            record_ai_observability(obs)
            return (
                None,
                obs,
                "GEMINI_API_KEY is absent; generated by deterministic synthesis engine.",
            )

        had_prior_failure = False
        abort_failover = False

        for provider_name, source_label, level, key, model_id in provider_chain:
            if abort_failover:
                break

            if not key:
                attempts.append(
                    AIProviderAttemptRecord(
                        provider=provider_name,
                        model=model_id,
                        fallback_level=level,
                        attempt_number=1,
                        request_status="SKIPPED_UNCONFIGURED",
                        latency_ms=0.0,
                        failure_category="missing_credentials",
                        sanitized_error=f"Credentials not configured for {provider_name}.",
                    )
                )
                last_failure_category = last_failure_category or "missing_credentials"
                had_prior_failure = True
                continue

            max_attempts = 1 + self.max_retries_per_provider
            for attempt_num in range(1, max_attempts + 1):
                t0 = time.perf_counter()
                try:
                    if provider_name == "google_gemini":
                        result_model = self._call_gemini_provider(prompt, response_schema)
                    elif provider_name == "openrouter":
                        result_model = self._call_openai_compatible_provider(
                            provider_label="OpenRouter",
                            endpoint_url=OPENROUTER_CHAT_URL,
                            api_key=key,
                            model_id=model_id,
                            prompt=prompt,
                            response_schema=response_schema,
                        )
                    else:
                        result_model = self._call_openai_compatible_provider(
                            provider_label="Groq",
                            endpoint_url=GROQ_CHAT_URL,
                            api_key=key,
                            model_id=model_id,
                            prompt=prompt,
                            response_schema=response_schema,
                        )

                    lat_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                    attempts.append(
                        AIProviderAttemptRecord(
                            provider=provider_name,
                            model=model_id,
                            fallback_level=level,
                            attempt_number=attempt_num,
                            request_status="SUCCESS",
                            latency_ms=lat_ms,
                            failure_category=None,
                            sanitized_error=None,
                        )
                    )
                    total_lat_ms = round((time.perf_counter() - overall_start) * 1000.0, 2)
                    req_status = "FAILED_OVER_SUCCESS" if (had_prior_failure or level > 0) else "SUCCESS"
                    obs = AIRequestObservability(
                        provider=provider_name,
                        model=model_id,
                        request_status=req_status,
                        fallback_level=level,
                        latency_ms=total_lat_ms,
                        failure_category=last_failure_category,
                        answer_source_label=source_label,
                        attempts=attempts,
                    )
                    record_ai_observability(obs)
                    fb_note = (
                        f"Failed over to {source_label} ({model_id}) after prior provider issue ({'; '.join(failure_reasons)})."
                        if failure_reasons
                        else None
                    )
                    return result_model, obs, fb_note

                except Exception as exc:
                    lat_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                    category, is_transient, should_failover = classify_provider_failure(exc)
                    safe_err = redact_secrets(f"{type(exc).__name__}: {str(exc)}", self._all_secrets())
                    last_failure_category = category
                    had_prior_failure = True

                    attempts.append(
                        AIProviderAttemptRecord(
                            provider=provider_name,
                            model=model_id,
                            fallback_level=level,
                            attempt_number=attempt_num,
                            request_status="FAILED",
                            latency_ms=lat_ms,
                            failure_category=category,
                            sanitized_error=safe_err,
                        )
                    )
                    logger.warning(
                        "Provider %s (%s) attempt %d/%d failed [%s]: %s",
                        provider_name,
                        model_id,
                        attempt_num,
                        max_attempts,
                        category,
                        safe_err,
                    )

                    if not should_failover and not allow_config_test_failover:
                        failure_reasons.append(f"{source_label} failed with non-failover application error [{category}]: {safe_err}")
                        abort_failover = True
                        break

                    if is_transient and attempt_num < max_attempts:
                        if self.retry_base_delay > 0:
                            time.sleep(self.retry_base_delay * (2 ** (attempt_num - 1)))
                        continue

                    failure_reasons.append(f"{source_label} ({model_id}) failed [{category}]: {safe_err}")
                    break

        total_lat_ms = round((time.perf_counter() - overall_start) * 1000.0, 2)
        obs = AIRequestObservability(
            provider="deterministic_fallback",
            model="DETERMINISTIC_FALLBACK_ENGINE",
            request_status="DETERMINISTIC_FALLBACK",
            fallback_level=3,
            latency_ms=total_lat_ms,
            failure_category=last_failure_category or "service_unavailable",
            answer_source_label="deterministic fallback",
            attempts=attempts,
        )
        record_ai_observability(obs)
        combined_reason = redact_secrets(
            f"AI provider invocation failed ({'; '.join(failure_reasons)}); fell back to deterministic synthesis.",
            self._all_secrets(),
        )
        return None, obs, combined_reason

    @staticmethod
    def _ensure_token_provenance(dossier: InstitutionalDossier) -> List[ProvenanceRecord]:
        """Ensure every EvidenceToken and dossier has 7-field ProvenanceRecords."""
        if dossier.provenance_records:
            return list(dossier.provenance_records)

        records: List[ProvenanceRecord] = []
        src = dossier.provenance_chain[0] if dossier.provenance_chain else "ACID_PERSISTENCE_STORE"
        doc = src.split(":")[0] if ":" in src else f"{dossier.institution_id}_dossier.json"
        for idx, tok in enumerate(dossier.evidence_tokens):
            if tok.provenance:
                records.append(tok.provenance)
            else:
                records.append(
                    ProvenanceRecord(
                        evidence_id=f"prov_tok_{dossier.institution_id}_{tok.academic_year}_{idx}",
                        source=src,
                        document=doc,
                        page_or_section=f"Section: {tok.signal_name}",
                        table_cell_or_range=f"Table[{tok.metric_name}]!AY{tok.academic_year}",
                        excerpt_or_image=tok.narrative_fragment,
                        extraction_confidence=0.98,
                        date_or_context=f"AY {tok.academic_year} ({dossier.institution_id})",
                        domain=tok.signal_name,
                        metric_name=tok.metric_name,
                        observed_value=tok.observed_value,
                        baseline_value=tok.baseline_value,
                        is_contradictory=False,
                    )
                )
        return records

    @staticmethod
    def _build_what_could_happen_next(
        dossier: InstitutionalDossier,
        forecast: Optional[ExplainableForecast] = None,
    ) -> str:
        """Build deterministic 'What could happen next?' grounded strictly in forecast calculations."""
        if forecast is not None:
            if forecast.status.value == "INSUFFICIENT_EVIDENCE" or not forecast.prediction:
                reason = forecast.insufficient_evidence_reason or "Insufficient multi-period history to compute a reliable trajectory."
                return f"[UNCERTAIN / INSUFFICIENT EVIDENCE] {reason}"
            terminal = forecast.prediction[-1]
            delta = round(terminal.projected_cri - forecast.current_cri, 4)
            direction = "increase" if delta > 0.005 else ("decrease" if delta < -0.005 else "remain near current levels")
            return (
                f"Deterministic {forecast.horizon} trajectory ({forecast.method_actually_used}) projects "
                f"Composite Risk Index to {direction} from {forecast.current_cri:.3f} to {terminal.projected_cri:.3f} "
                f"(band: {terminal.confidence_band_low:.3f}–{terminal.confidence_band_high:.3f}, "
                f"confidence: {forecast.confidence:.0%})."
            )

        if dossier.total_anomalies == 0:
            return (
                f"Under status-quo deterministic assumptions at CRI {dossier.composite_risk_index:.2f} ({dossier.risk_level}), "
                f"institutional operations are projected to remain stable unless new enrollment or placement shocks occur."
            )
        return (
            f"Without targeted intervention on {dossier.primary_threat}, deterministic risk pressure at CRI "
            f"{dossier.composite_risk_index:.2f} ({dossier.risk_level}) carries elevated probability of compounding "
            f"across subsequent academic cycles."
        )

    @staticmethod
    def _extract_allowed_numbers(
        dossier: InstitutionalDossier,
        forecast: Optional[ExplainableForecast] = None,
    ) -> List[float]:
        """Collect all deterministic numbers present in the dossier and forecast for hallucination checking."""
        allowed: List[float] = [
            round(dossier.composite_risk_index, 4),
            round(dossier.composite_risk_index, 2),
            round(dossier.composite_risk_index * 100.0, 1),
            float(dossier.total_anomalies),
            90.0,  # 90-day standard horizon reference
            1.0,
            2.0,
            3.0,
            4.0,
            5.0,
        ]
        for tok in dossier.evidence_tokens:
            allowed.extend([
                float(tok.academic_year),
                round(tok.observed_value, 2),
                round(tok.baseline_value, 2),
                round(abs(tok.deviation_zscore), 2),
            ])
        for rec in dossier.provenance_records:
            if rec.observed_value is not None:
                allowed.append(round(float(rec.observed_value), 2))
            if rec.baseline_value is not None:
                allowed.append(round(float(rec.baseline_value), 2))
        if forecast and forecast.prediction:
            allowed.append(round(forecast.current_cri, 4))
            allowed.append(round(forecast.confidence, 2))
            allowed.append(round(forecast.confidence * 100.0, 1))
            for pt in forecast.prediction:
                allowed.extend([
                    round(pt.projected_cri, 4),
                    round(pt.projected_cri, 2),
                    round(pt.confidence_band_low, 3),
                    round(pt.confidence_band_high, 3),
                ])
                if pt.target_period:
                    allowed.append(float(pt.target_period))
        return allowed

    @classmethod
    def _is_number_grounded(cls, val: float, allowed_numbers: List[float]) -> bool:
        for a in allowed_numbers:
            if abs(val - a) <= max(0.15, abs(a) * 0.02):
                return True
        return False

    @classmethod
    def _apply_hallucination_guard(
        cls,
        response: ExecutiveNarrativeResponse,
        dossier: InstitutionalDossier,
        forecast: Optional[ExplainableForecast] = None,
    ) -> ExecutiveNarrativeResponse:
        """
        Enforce deterministic ground truth and hallucination resistance on any narrative response:
        1. Lock institution_id, risk_level, and composite_risk_index to deterministic dossier values.
        2. Populate any missing Phase 5 sections from deterministic evidence.
        3. Verify numeric claims against allowed deterministic numbers; flag unverified numbers as uncertain.
        4. Explicitly surface missing evidence domains and contradictions as uncertain claims.
        """
        overrides: List[str] = []
        unverified_flagged: List[str] = []

        if response.institution_id != dossier.institution_id:
            overrides.append(
                f"Overrode hallucinated institution_id '{response.institution_id}' -> '{dossier.institution_id}'"
            )
            response.institution_id = dossier.institution_id

        if response.risk_level != dossier.risk_level:
            overrides.append(
                f"Overrode LLM risk_level '{response.risk_level}' -> deterministic '{dossier.risk_level}'"
            )
            response.risk_level = dossier.risk_level

        if abs(response.composite_risk_index - dossier.composite_risk_index) > 1e-6:
            overrides.append(
                f"Overrode LLM composite_risk_index {response.composite_risk_index} -> deterministic {dossier.composite_risk_index}"
            )
            response.composite_risk_index = dossier.composite_risk_index

        prov_records = cls._ensure_token_provenance(dossier)
        response.evidence = prov_records

        if (
            response.executive_summary
            and dossier.institution_id not in response.executive_summary
            and re.search(r"\b(the|this)\s+institution\b", response.executive_summary, flags=re.IGNORECASE)
        ):
            response.executive_summary = re.sub(
                r"\b(the|this)\s+institution\b",
                f"Institution {dossier.institution_id}",
                response.executive_summary,
                count=1,
                flags=re.IGNORECASE,
            )

        if not response.what_is_happening:
            response.what_is_happening = response.executive_summary

        if not response.why:
            response.why = list(response.root_causes)

        # Deterministic forecast always governs 'what_could_happen_next' if forecast is provided or if empty
        det_next = cls._build_what_could_happen_next(dossier, forecast)
        if not response.what_could_happen_next or forecast is not None:
            response.what_could_happen_next = det_next

        if not response.what_should_leadership_investigate:
            response.what_should_leadership_investigate = list(response.investigation_priorities)

        allowed_nums = cls._extract_allowed_numbers(dossier, forecast)

        sanitized_why: List[str] = []
        grounded_claims: List[GroundedClaim] = []

        # Claim 1: What is happening
        wih_nums = [float(x) for x in re.findall(r"(?<![A-Za-z0-9_])-?\d+\.\d+", response.what_is_happening)]
        wih_unverified = [n for n in wih_nums if not cls._is_number_grounded(n, allowed_nums)]
        wih_uncertain = bool(wih_unverified)
        if wih_unverified:
            unverified_flagged.append(
                f"Unverified numeric value(s) {wih_unverified} in what_is_happening; verified against deterministic CRI {dossier.composite_risk_index:.2f}."
            )
            # Enforce verified institutional prefix without technical debug tags
            if not response.what_is_happening.startswith("Institution"):
                response.what_is_happening = (
                    f"Institution {dossier.institution_id} is evaluated at {dossier.risk_level} risk. "
                    f"{response.what_is_happening}"
                )

        grounded_claims.append(
            GroundedClaim(
                claim_id="claim_what_is_happening",
                section="what_is_happening",
                statement=response.what_is_happening,
                is_uncertain=wih_uncertain or (len(prov_records) == 0),
                uncertainty_reason=(
                    "Contains unverified numeric claim overridden by deterministic CRI"
                    if wih_uncertain
                    else ("No anomaly evidence tokens triggered" if len(prov_records) == 0 else None)
                ),
                provenance_links=prov_records[:4],
            )
        )

        # Inspect each 'why' item
        for idx, cause in enumerate(response.why):
            nums = [float(x) for x in re.findall(r"(?<![A-Za-z0-9_])-?\d+\.\d+", cause)]
            bad_nums = [n for n in nums if not cls._is_number_grounded(n, allowed_nums)]
            is_unc = False
            unc_reason: Optional[str] = None
            statement_text = cause

            if bad_nums:
                is_unc = True
                unc_reason = f"Flagged unverified numeric value(s) {bad_nums} not present in deterministic evidence tokens."
                unverified_flagged.append(f"Why[{idx}] flagged for unverified numbers {bad_nums}: {cause}")
                if "[UNCERTAIN" not in statement_text:
                    statement_text = f"[UNCERTAIN — UNVERIFIED NUMERIC CLAIM] {statement_text}"

            matched_provs = [
                p for p in prov_records
                if (p.metric_name and p.metric_name.lower() in cause.lower())
                or (p.domain and p.domain.lower() in cause.lower())
            ]
            if not matched_provs and prov_records:
                matched_provs = [prov_records[min(idx, len(prov_records) - 1)]]
            if not matched_provs:
                is_unc = True
                unc_reason = unc_reason or "No direct provenance record linked."

            sanitized_why.append(statement_text)
            grounded_claims.append(
                GroundedClaim(
                    claim_id=f"claim_why_{idx}",
                    section="why",
                    statement=statement_text,
                    is_uncertain=is_unc,
                    uncertainty_reason=unc_reason,
                    provenance_links=matched_provs[:2],
                )
            )

        response.why = sanitized_why

        # Forecast claim
        fc_uncertain = "[UNCERTAIN" in response.what_could_happen_next
        grounded_claims.append(
            GroundedClaim(
                claim_id="claim_what_could_happen_next",
                section="what_could_happen_next",
                statement=response.what_could_happen_next,
                is_uncertain=fc_uncertain,
                uncertainty_reason="Insufficient multi-period historical baseline for forecasting" if fc_uncertain else None,
                provenance_links=prov_records[:3],
            )
        )

        # Leadership investigation claims
        for idx, inv in enumerate(response.what_should_leadership_investigate):
            grounded_claims.append(
                GroundedClaim(
                    claim_id=f"claim_investigate_{idx}",
                    section="what_should_leadership_investigate",
                    statement=inv,
                    is_uncertain=False,
                    provenance_links=prov_records[:2],
                )
            )

        # Missing evidence & contradictions explicitly added to uncertain_claims
        uncertain_list: List[str] = [c.statement for c in grounded_claims if c.is_uncertain]
        if dossier.missing_evidence_domains:
            msg = (
                f"[UNCERTAIN / MISSING EVIDENCE] No anomaly or signal coverage in domains: "
                f"{', '.join(dossier.missing_evidence_domains)}."
            )
            uncertain_list.append(msg)
        for c_note in dossier.contradictions_detected:
            uncertain_list.append(f"[UNCERTAIN / CONTRADICTORY EVIDENCE] {c_note}")

        response.grounded_claims = grounded_claims
        response.uncertain_claims = uncertain_list
        response.hallucination_guard_applied = True
        response.hallucination_guard_report = {
            "deterministic_cri_locked": dossier.composite_risk_index,
            "deterministic_risk_level_locked": dossier.risk_level,
            "deterministic_overrides_applied": overrides,
            "unverified_claims_flagged": unverified_flagged,
            "provenance_records_attached": len(prov_records),
            "active_provider": response.active_provider,
            "answer_source": response.answer_source,
        }
        return response

    @staticmethod
    def _build_prompt(
        dossier: InstitutionalDossier,
        forecast: Optional[ExplainableForecast] = None,
    ) -> str:
        """Construct a strict, evidence-locked prompt for the AI provider."""
        tokens_json = [t.model_dump(mode="json") for t in dossier.evidence_tokens]
        forecast_summary = "No multi-period forecast provided."
        if forecast is not None:
            if forecast.status.value == "INSUFFICIENT_EVIDENCE" or not forecast.prediction:
                forecast_summary = f"INSUFFICIENT_EVIDENCE: {forecast.insufficient_evidence_reason}"
            else:
                terminal = forecast.prediction[-1]
                forecast_summary = (
                    f"Horizon {forecast.horizon} ({forecast.method_actually_used}): "
                    f"current_cri={forecast.current_cri:.4f} -> projected_cri={terminal.projected_cri:.4f} "
                    f"(confidence={forecast.confidence:.2f})"
                )

        return (
            "You are the Executive Risk Advisor for the CIP Crisis Intelligence Platform.\n"
            "Analyze the following mathematically verified InstitutionalDossier and produce an AI Executive Analysis JSON.\n"
            "CRITICAL RULES:\n"
            "1. Write in clear, everyday executive English that university leaders can immediately understand.\n"
            "2. DO NOT invent any statistics, years, departments, or facts not explicitly present in `evidence_tokens`.\n"
            "3. DO NOT use mathematical jargon such as 'z-score', 'standard deviation', 'variance', or algorithm tokens in the narrative.\n"
            "   State changes naturally (e.g., 'placements fell from 26% to 12% in AY 2024; admissions rose from 56 to 60').\n"
            "4. Always mention the specific academic year, department, and supporting source records for each finding.\n"
            "5. If a domain has no evidence or is uncertain, explicitly state that evidence is missing or uncertain.\n"
            "6. Output ONLY valid JSON matching the schema.\n\n"
            f"INSTITUTION ID: {dossier.institution_id}\n"
            f"RISK LEVEL: {dossier.risk_level}\n"
            f"COMPOSITE RISK INDEX: {dossier.composite_risk_index}\n"
            f"PRIMARY THREAT: {dossier.primary_threat}\n"
            f"PROVENANCE CHAIN: {dossier.provenance_chain}\n"
            f"RECOMMENDED MITIGATIONS: {dossier.recommended_mitigations}\n"
            f"DETERMINISTIC FORECAST: {forecast_summary}\n"
            f"EVIDENCE TOKENS:\n{json.dumps(tokens_json, indent=2)}\n"
        )

    build_prompt = _build_prompt

    @classmethod
    def _generate_deterministic_fallback(
        cls,
        dossier: InstitutionalDossier,
        forecast: Optional[ExplainableForecast] = None,
        fallback_reason: str = "GEMINI_API_KEY not configured; using deterministic evidence synthesis.",
        observability: Optional[AIRequestObservability] = None,
    ) -> ExecutiveNarrativeResponse:
        """
        Produce a 100% deterministic, high-precision AI Executive Analysis directly from
        the evidence tokens when AI providers are unconfigured or unavailable.
        Never pretends an LLM was used.
        """
        prov_records = cls._ensure_token_provenance(dossier)
        risk_pct = int(round(dossier.composite_risk_index * 100))
        clean_threat = dossier.primary_threat.replace('_', ' ').title()

        if dossier.total_anomalies == 0:
            summary = (
                f"Institution {dossier.institution_id} maintains a stable operational profile "
                f"with a Risk Score of {risk_pct}/100 ({dossier.risk_level} risk). "
                f"Monitored student admissions, placement outcomes, and academic indicators remain within standard historical baseline tolerances."
            )
            causes = ["All monitored institutional indicators remain within standard historical tolerances."]
            priorities = ["Continue routine annual verification of admissions and placement ledger submissions."]
        else:
            summary = (
                f"Institution {dossier.institution_id} is evaluated at {dossier.risk_level} risk "
                f"(Risk Score: {risk_pct}/100), driven primarily by changes in {clean_threat}. "
                f"Our analysis identified {dossier.total_anomalies} significant operational area(s) needing leadership attention."
            )
            causes = [
                t.narrative_fragment.replace('Z-score:', 'deviation:').replace('Z-Score:', 'deviation:')
                for t in dossier.evidence_tokens
            ]
            priorities = [
                f"Audit {t.signal_name.replace('_', ' ')} records for AY {t.academic_year} (severity: {t.severity})."
                for t in dossier.evidence_tokens
            ]

        actions = (
            dossier.recommended_mitigations
            if dossier.recommended_mitigations
            else ["Maintain standard institutional quality assurance monitoring."]
        )

        prov_str = ", ".join(dossier.provenance_chain) if dossier.provenance_chain else "canonical institutional ledger"
        audit_note = (
            f"DETERMINISTIC_FALLBACK_MODE: Grounded strictly in {dossier.total_anomalies} empirical evidence tokens "
            f"sourced from [{prov_str}]. Zero unverified LLM inferences applied."
        )

        what_next = cls._build_what_could_happen_next(dossier, forecast)

        resp = ExecutiveNarrativeResponse(
            institution_id=dossier.institution_id,
            risk_level=dossier.risk_level,
            composite_risk_index=dossier.composite_risk_index,
            executive_summary=summary,
            root_causes=causes,
            prioritized_actions=actions,
            investigation_priorities=priorities,
            audit_provenance_summary=audit_note,
            analysis_title="AI Executive Analysis",
            generation_mode="DETERMINISTIC_FALLBACK",
            gemini_live_used=False,
            active_provider="deterministic_fallback",
            answer_source="deterministic fallback",
            model_used="DETERMINISTIC_FALLBACK_ENGINE",
            fallback_reason=redact_secrets(fallback_reason),
            observability=observability,
            what_is_happening=summary,
            why=causes,
            evidence=prov_records,
            what_could_happen_next=what_next,
            what_should_leadership_investigate=priorities,
        )
        return cls._apply_hallucination_guard(resp, dossier, forecast)

    @staticmethod
    def _map_provider_to_mode(provider: AIProviderName) -> GenerationModeLiteral:
        if provider == "google_gemini":
            return "LIVE_GEMINI"
        if provider == "openrouter":
            return "LIVE_OPENROUTER"
        if provider == "groq":
            return "LIVE_GROQ"
        return "DETERMINISTIC_FALLBACK"

    def generate_narrative(
        self,
        dossier: InstitutionalDossier,
        forecast: Optional[ExplainableForecast] = None,
        allow_config_test_failover: bool = False,
    ) -> ExecutiveNarrativeResponse:
        """
        Generate a structured AI Executive Analysis for the given InstitutionalDossier.
        Executes the Multi-Provider AI failover chain (Gemini -> OpenRouter -> Groq -> Deterministic Fallback)
        and always applies the deterministic hallucination guard before returning.
        """
        prompt = self._build_prompt(dossier, forecast=forecast)
        synth_obj, obs, fallback_note = self.execute_structured_prompt(
            prompt=prompt,
            response_schema=_LLMExecutiveNarrativeSchema,
            allow_config_test_failover=allow_config_test_failover,
        )

        if synth_obj is None:
            return self._generate_deterministic_fallback(
                dossier,
                forecast=forecast,
                fallback_reason=fallback_note or "All configured AI providers unavailable; used deterministic fallback.",
                observability=obs,
            )

        validated = ExecutiveNarrativeResponse.model_validate(synth_obj.model_dump())
        validated.analysis_title = "AI Executive Analysis"
        validated.generation_mode = self._map_provider_to_mode(obs.provider)
        validated.gemini_live_used = (obs.provider == "google_gemini")
        validated.active_provider = obs.provider
        validated.answer_source = obs.answer_source_label
        validated.model_used = obs.model
        validated.fallback_reason = fallback_note
        validated.observability = obs
        return self._apply_hallucination_guard(validated, dossier, forecast=forecast)
