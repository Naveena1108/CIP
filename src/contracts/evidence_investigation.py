"""
CIP Phase 5 Contracts: Evidence-Grounded AI, Deep Provenance, Natural-Language Investigation,
and Multi-Provider AI Configuration & Observability (Google Gemini -> OpenRouter -> Groq -> Deterministic Fallback).

Enforces:
- 7-field provenance linking for every major finding
- Structured natural-question investigation responses
- Multi-provider AI observability (provider, model, request_status, fallback_level, latency_ms, failure_category)
- Truthful identification of whether an answer came from Gemini, OpenRouter, Groq, or deterministic fallback
- Hallucination resistance metadata
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


GenerationModeLiteral = Literal[
    "LIVE_GEMINI",
    "LIVE_OPENROUTER",
    "LIVE_GROQ",
    "DETERMINISTIC_FALLBACK",
]

AIProviderName = Literal[
    "google_gemini",
    "openrouter",
    "groq",
    "deterministic_fallback",
]

AnswerSourceLabel = Literal[
    "Gemini",
    "OpenRouter",
    "Groq",
    "deterministic fallback",
]


class AIProviderAttemptRecord(BaseModel):
    """
    Observability record for a single provider attempt within the multi-provider fallback chain.
    Never stores or exposes API credentials.
    """
    provider: AIProviderName
    model: str
    fallback_level: int = Field(..., ge=0, le=3, description="0=Primary (Gemini), 1=Secondary (OpenRouter), 2=Tertiary (Groq), 3=Deterministic")
    attempt_number: int = Field(default=1, ge=1)
    request_status: Literal["SUCCESS", "FAILED", "SKIPPED_UNCONFIGURED"]
    latency_ms: float = Field(default=0.0, ge=0.0)
    failure_category: Optional[str] = Field(
        default=None,
        description="Normalized failure category: rate_limit, timeout, service_unavailable, server_error, invalid_credentials, malformed_request, invalid_schema, malformed_response, missing_credentials",
    )
    sanitized_error: Optional[str] = Field(
        default=None,
        description="Secret-redacted error summary if attempt failed",
    )


class AIRequestObservability(BaseModel):
    """
    Per-request observability telemetry for CIP AI requests.
    Tracks provider, model, request_status, fallback_level, latency_ms, and failure_category.
    Never contains API keys.
    """
    provider: AIProviderName
    model: str
    request_status: Literal["SUCCESS", "FAILED_OVER_SUCCESS", "DETERMINISTIC_FALLBACK"]
    fallback_level: int = Field(..., ge=0, le=3, description="0=Primary Gemini, 1=Secondary OpenRouter, 2=Tertiary Groq, 3=Deterministic Fallback")
    latency_ms: float = Field(default=0.0, ge=0.0)
    failure_category: Optional[str] = Field(default=None)
    answer_source_label: AnswerSourceLabel = Field(
        default="deterministic fallback",
        description="Human-readable source identifier: 'Gemini', 'OpenRouter', 'Groq', or 'deterministic fallback'",
    )
    attempts: List[AIProviderAttemptRecord] = Field(default_factory=list)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProvenanceRecord(BaseModel):
    """
    Deep provenance record for CIP Phase 5.
    Every major finding links to available provenance with all 7 required fields:
    1. source
    2. document
    3. page_or_section (page/section)
    4. table_cell_or_range (table/cell/range)
    5. excerpt_or_image (excerpt/image)
    6. extraction_confidence
    7. date_or_context (date/context)
    """
    evidence_id: str = Field(..., description="Unique provenance token identifier")
    source: str = Field(..., description="Source channel, dataset, or ingestion pipeline identifier")
    document: str = Field(..., description="Source document filename, report title, or canonical table")
    page_or_section: str = Field(..., description="Page number, slide number, or document section heading")
    table_cell_or_range: str = Field(..., description="Spreadsheet sheet/row/cell range or table coordinate")
    excerpt_or_image: str = Field(..., description="Verbatim text excerpt, row snapshot, or image/scan reference")
    extraction_confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence of extraction (0.0 to 1.0)")
    date_or_context: str = Field(..., description="Academic year, date, department, and institutional context")
    domain: Optional[str] = Field(default=None, description="Institutional signal domain")
    metric_name: Optional[str] = Field(default=None, description="Canonical or discovered metric name")
    observed_value: Optional[float] = Field(default=None, description="Observed numeric value if applicable")
    baseline_value: Optional[float] = Field(default=None, description="Historical baseline value if applicable")
    is_contradictory: bool = Field(default=False, description="True if conflicting source values exist for this metric/period")
    contradiction_detail: Optional[str] = Field(default=None, description="Explanation of contradictory source values if present")


class GroundedClaim(BaseModel):
    """
    A single claim within AI Executive Analysis or Investigation.
    Every claim must either link to supporting ProvenanceRecord items or be explicitly marked uncertain.
    """
    claim_id: str
    section: Literal[
        "what_is_happening",
        "why",
        "evidence",
        "what_could_happen_next",
        "what_should_leadership_investigate",
        "investigation_finding",
    ]
    statement: str
    is_uncertain: bool = Field(
        default=False,
        description="True when evidence is missing, insufficient, or contradictory",
    )
    uncertainty_reason: Optional[str] = Field(
        default=None,
        description="Explicit reason why the claim is marked uncertain",
    )
    provenance_links: List[ProvenanceRecord] = Field(
        default_factory=list,
        description="Supporting provenance records grounding this claim",
    )


class InsightWithEvidence(BaseModel):
    """
    A clickable major finding / insight paired with its supporting 7-field provenance records.
    """
    insight_id: str
    title: str
    category: Literal[
        "anomaly",
        "risk_progression",
        "cross_signal",
        "forecast",
        "contradiction_or_quality",
        "executive_claim",
    ]
    severity_or_stage: str
    summary: str
    department: Optional[str] = None
    academic_year: Optional[int] = None
    is_uncertain: bool = False
    uncertainty_reason: Optional[str] = None
    why_it_matters: Optional[str] = None
    what_to_check: Optional[str] = None
    source: Optional[str] = None
    technical_details: Optional[Dict[str, Any]] = None
    provenance: List[ProvenanceRecord] = Field(default_factory=list)


class EvidenceProvenanceCatalog(BaseModel):
    """
    Complete catalog of major institutional insights and their clickable 7-field provenance records.
    """
    institution_id: str
    organization_id: Optional[str] = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    total_insights: int
    total_provenance_records: int
    insights: List[InsightWithEvidence] = Field(default_factory=list)
    all_provenance_records: List[ProvenanceRecord] = Field(default_factory=list)


class NaturalQuestionRequest(BaseModel):
    """
    Request payload for natural-language institutional investigation.
    """
    question: str = Field(
        ...,
        min_length=2,
        description="Natural language question, e.g., 'Why did retention decline?' or 'Why did placements fall?'",
    )


class NaturalQuestionInvestigationResponse(BaseModel):
    """
    CIP Phase 5 Structured Response for Natural-Language Investigation.
    Exposes finding, evidence, related_signals, potential_contributing_factors,
    alternative_explanations, confidence, missing_information, provider source, and observability.
    """
    institution_id: str
    organization_id: Optional[str] = None
    question: str
    detected_intent: str = Field(
        ...,
        description="Normalized question intent (e.g., retention_decline, placement_decline, what_changed, admissions_vacancy, finance_or_faculty, general_investigation)",
    )
    finding: str = Field(..., description="Primary evidence-grounded finding answering the question")
    evidence: List[ProvenanceRecord] = Field(
        default_factory=list,
        description="7-field provenance records supporting the finding",
    )
    related_signals: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Related institutional signals inspected across domains",
    )
    potential_contributing_factors: List[str] = Field(
        default_factory=list,
        description="Evidenced potential contributing factors (strictly framed as non-causal unless proven)",
    )
    alternative_explanations: List[str] = Field(
        default_factory=list,
        description="Plausible alternative explanations and non-crisis interpretations",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score grounded in evidence coverage, extraction quality, and consistency",
    )
    missing_information: List[str] = Field(
        default_factory=list,
        description="Explicit list of missing domains, periods, or unverified context needed for fuller certainty",
    )
    contradictions_noted: List[str] = Field(
        default_factory=list,
        description="Explicitly surfaced contradictory evidence or conflicting source values",
    )
    generation_mode: GenerationModeLiteral = Field(
        default="DETERMINISTIC_FALLBACK",
        description="Truthful indicator of whether LIVE_GEMINI, LIVE_OPENROUTER, LIVE_GROQ, or DETERMINISTIC_FALLBACK produced the synthesis",
    )
    gemini_live_used: bool = Field(
        default=False,
        description="True when a live Gemini API call succeeded and passed deterministic validation",
    )
    active_provider: AIProviderName = Field(
        default="deterministic_fallback",
        description="Identifier of the provider that produced the answer: google_gemini, openrouter, groq, or deterministic_fallback",
    )
    answer_source: AnswerSourceLabel = Field(
        default="deterministic fallback",
        description="Whether the answer came from Gemini, OpenRouter, Groq, or deterministic fallback",
    )
    model_used: str = Field(
        default="DETERMINISTIC_FALLBACK_ENGINE",
        description="Exact model identifier or deterministic engine identifier",
    )
    fallback_reason: Optional[str] = Field(
        default=None,
        description="Truthful explanation when failover or deterministic fallback occurred",
    )
    observability: Optional[AIRequestObservability] = Field(
        default=None,
        description="Per-request observability tracking provider, model, request_status, fallback_level, latency_ms, and failure_category",
    )
    hallucination_guard_applied: bool = Field(
        default=True,
        description="True when deterministic post-validation verified numbers and stripped ungrounded claims",
    )
    hallucination_guard_report: Dict[str, Any] = Field(
        default_factory=dict,
        description="Audit log of deterministic locks and any unverified LLM claims sanitized or flagged",
    )


class ProviderConfigurationInfo(BaseModel):
    """Configuration status of a single AI provider (never includes API keys)."""
    provider: AIProviderName
    priority_rank: int = Field(..., ge=1, le=3, description="1=Primary, 2=Secondary, 3=Tertiary")
    configured_model: str
    credentials_present: bool


class GeminiStatusResponse(BaseModel):
    """
    Truthful runtime status of CIP Multi-Provider AI integration (Google Gemini -> OpenRouter -> Groq -> Deterministic Fallback).
    Never exposes API credentials.
    """
    gemini_available: bool
    generation_mode: GenerationModeLiteral
    configured_model: str
    credentials_present: bool
    status_explanation: str
    fallback_order: List[str] = Field(
        default_factory=lambda: [
            "1. Primary: Google Gemini (gemini-3.8-flash)",
            "2. Secondary: OpenRouter (google/gemini-3.8-flash)",
            "3. Tertiary: Groq (openai/gpt-oss-120b)",
            "4. Final Fallback: CIP Deterministic Engine",
        ]
    )
    providers_configured: List[ProviderConfigurationInfo] = Field(default_factory=list)
    recent_observability: List[AIRequestObservability] = Field(default_factory=list)
    deterministic_responsibilities: List[str] = Field(
        default_factory=lambda: [
            "Canonical and dynamic metric calculations",
            "Historical baseline computation and insufficiency detection",
            "Multi-dimensional anomaly Z-scores and materiality scoring",
            "Composite Risk Index (CRI) and 5-stage risk progression",
            "Autoregressive trajectory forecasts",
        ]
    )
    gemini_responsibilities: List[str] = Field(
        default_factory=lambda: [
            "Unstructured document semantic understanding",
            "Semantic signal extraction assistance",
            "Cross-domain evidence synthesis",
            "AI Executive Analysis narrative explanation",
            "Natural-language question investigation synthesis",
            "Executive summarization strictly bounded by deterministic ground truth",
        ]
    )
