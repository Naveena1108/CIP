"""
CIP Phase 4 Contracts: Explainable Forecasting and Institutional Memory.

Enforces:
1. Explainable Forecasting:
   - Exposes prediction, horizon, method_actually_used, input_signals, historical_evidence,
     data_coverage, confidence, limitations, alternative_explanations, trajectory_drivers.
   - Explicit INSUFFICIENT_EVIDENCE state when historical data is insufficient (< 2 periods).
   - Deterministic "Why did the prediction change?" attributing forecast shifts to exact changed inputs.
   - Zero LLM invention of forecast values or methodology.
2. Institutional Memory & Learning Loop:
   - Distinct persisted categories: observed_fact, analysis, inference, prediction,
     outcome, user_feedback, unknown.
   - Stores historical_signals, baselines, previous_analyses, predictions, interventions,
     outcomes, validated_rejected_hypotheses, unresolved_questions.
   - Longitudinal learning loop: Prediction -> later observation -> outcome comparison ->
     prediction accuracy -> institutional learning.
   - User feedback (Confirmed / Incorrect / Insufficient Evidence) stored strictly as
     user_feedback and never blindly converted into observed_fact.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class ForecastStatus(str, Enum):
    SUFFICIENT_EVIDENCE = "SUFFICIENT_EVIDENCE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class MemoryCategory(str, Enum):
    OBSERVED_FACT = "observed_fact"
    ANALYSIS = "analysis"
    INFERENCE = "inference"
    PREDICTION = "prediction"
    OUTCOME = "outcome"
    USER_FEEDBACK = "user_feedback"
    UNKNOWN = "unknown"


class FeedbackVerdict(str, Enum):
    CONFIRMED = "Confirmed"
    INCORRECT = "Incorrect"
    INSUFFICIENT_EVIDENCE = "Insufficient Evidence"


class TrajectoryDriver(BaseModel):
    """Mathematical driver contributing to the projected risk trajectory."""
    signal_or_slope: str
    slope_value: float
    weight: float
    weighted_contribution: float
    direction: Literal["WORSENING_RISK", "IMPROVING_RISK", "NEUTRAL"]
    explanation: str


class ForecastDataCoverage(BaseModel):
    """Explicit audit of historical data coverage backing a forecast."""
    distinct_periods: int
    years_covered: List[int] = Field(default_factory=list)
    domains_covered: List[str] = Field(default_factory=list)
    total_observations: int = 0
    minimum_required_periods: int = 2
    coverage_ratio: float = Field(ge=0.0, le=1.0)
    is_sufficient: bool


class ForecastTrajectoryPoint(BaseModel):
    """Single future period projection with confidence bands."""
    year_offset: int
    target_period: Optional[int] = None
    projected_cri: float = Field(ge=0.0, le=1.0)
    confidence_band_low: float = Field(ge=0.0, le=1.0)
    confidence_band_high: float = Field(ge=0.0, le=1.0)
    scenario: str = "STATUS_QUO"


class ChangedForecastInput(BaseModel):
    """Identifies an exact input parameter that changed between two forecasts."""
    input_name: str
    previous_value: float
    current_value: float
    delta: float
    impact_on_projected_cri: float
    explanation: str


class ForecastChangeExplanation(BaseModel):
    """
    Answers: "Why did the prediction change?"
    Strictly identifies actual changed inputs and their deterministic mathematical contribution.
    """
    institution_id: str
    has_previous_prediction: bool
    previous_current_cri: Optional[float] = None
    new_current_cri: Optional[float] = None
    previous_terminal_cri: Optional[float] = None
    current_terminal_cri: Optional[float] = None
    terminal_cri_delta: Optional[float] = None
    changed_inputs: List[ChangedForecastInput] = Field(default_factory=list)
    newly_added_periods: List[int] = Field(default_factory=list)
    summary: str
    deterministic_attribution: str


class ExplainableForecast(BaseModel):
    """
    CIP Phase 4 Explainable Forecast.
    Never fabricates a precise prediction when historical evidence is insufficient.
    Never allows an LLM to invent forecast values or methodology.
    """
    institution_id: str
    organization_id: Optional[str] = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: ForecastStatus
    insufficient_evidence_reason: Optional[str] = None
    prediction: Optional[List[ForecastTrajectoryPoint]] = None
    current_cri: Optional[float] = None
    horizon: str
    years_forward: int
    method_actually_used: str
    input_signals: Dict[str, Any] = Field(default_factory=dict)
    historical_evidence: List[str] = Field(default_factory=list)
    data_coverage: ForecastDataCoverage
    confidence: float = Field(ge=0.0, le=1.0)
    limitations: List[str] = Field(default_factory=list)
    alternative_explanations: List[str] = Field(default_factory=list)
    trajectory_drivers: List[TrajectoryDriver] = Field(default_factory=list)
    why_did_prediction_change: Optional[ForecastChangeExplanation] = None


class InstitutionalMemoryEntry(BaseModel):
    """
    Single persisted record in Institutional Memory with strict epistemic category separation:
    observed_fact, analysis, inference, prediction, outcome, user_feedback, unknown.
    """
    memory_id: str
    institution_id: str
    organization_id: Optional[str] = None
    category: MemoryCategory
    domain: str
    metric_or_topic: str
    academic_year: Optional[int] = None
    statement: str
    confidence: Optional[float] = None
    evidence_refs: List[str] = Field(default_factory=list)
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PredictionOutcomeComparisonRequest(BaseModel):
    """Request payload to compare a prediction with a later observed value."""
    prediction_memory_id: Optional[str] = None
    target_academic_year: int
    metric_name: str = "composite_risk_index"
    predicted_value: Optional[float] = None
    later_observed_value: Optional[float] = None
    confidence_band_low: Optional[float] = None
    confidence_band_high: Optional[float] = None
    notes: Optional[str] = None


class PredictionOutcomeComparison(BaseModel):
    """
    Longitudinal learning loop record:
    Prediction -> later observation -> outcome comparison -> prediction accuracy -> institutional learning.
    """
    comparison_id: str
    institution_id: str
    prediction_memory_id: Optional[str] = None
    target_academic_year: int
    metric_name: str
    predicted_value: float
    confidence_band_low: Optional[float] = None
    confidence_band_high: Optional[float] = None
    later_observed_value: float
    signed_error: float
    absolute_error: float
    within_confidence_band: bool
    prediction_accuracy: float = Field(ge=0.0, le=1.0)
    outcome_summary: str
    institutional_learning: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class UserFeedbackSubmission(BaseModel):
    """
    User feedback submission on an analytical finding, inference, hypothesis, or prediction.
    Supported verdicts: Confirmed, Incorrect, Insufficient Evidence.
    """
    target_id: str
    target_category: str = "inference"
    verdict: FeedbackVerdict
    reviewer_notes: Optional[str] = None
    domain: str = "institutional"
    metric_or_topic: str = "general_finding"
    academic_year: Optional[int] = None


class UserFeedbackRecord(BaseModel):
    """
    Persisted user feedback entry.
    Strictly stored as 'user_feedback' and NEVER blindly converted into 'observed_fact'.
    """
    feedback_id: str
    institution_id: str
    target_id: str
    target_category: str
    verdict: FeedbackVerdict
    stored_category: Literal["user_feedback"] = "user_feedback"
    converted_to_observed_fact: bool = False
    domain: str = "institutional"
    metric_or_topic: str = "general_finding"
    academic_year: Optional[int] = None
    reviewer_notes: Optional[str] = None
    submitted_by_user_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class HypothesisRecord(BaseModel):
    """Validated, rejected, or under-review institutional hypothesis."""
    hypothesis_id: str
    domain: str
    metric_or_topic: str
    statement: str
    status: Literal["VALIDATED", "REJECTED", "UNDER_REVIEW", "INSUFFICIENT_EVIDENCE"]
    supporting_evidence: List[str] = Field(default_factory=list)
    feedback_verdict: Optional[FeedbackVerdict] = None


class InstitutionalMemoryStoreView(BaseModel):
    """
    Complete longitudinal Institutional Memory view for an institution.
    Exposes both the 7 epistemic/feedback categories and the 8 structured institutional collections.
    """
    institution_id: str
    organization_id: Optional[str] = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    total_entries: int = 0
    category_counts: Dict[str, int] = Field(default_factory=dict)
    entries_by_category: Dict[str, List[InstitutionalMemoryEntry]] = Field(default_factory=dict)
    historical_signals: List[Dict[str, Any]] = Field(default_factory=list)
    baselines: List[Dict[str, Any]] = Field(default_factory=list)
    previous_analyses: List[Dict[str, Any]] = Field(default_factory=list)
    predictions: List[Dict[str, Any]] = Field(default_factory=list)
    interventions: List[Dict[str, Any]] = Field(default_factory=list)
    outcomes: List[PredictionOutcomeComparison] = Field(default_factory=list)
    validated_rejected_hypotheses: List[HypothesisRecord] = Field(default_factory=list)
    unresolved_questions: List[str] = Field(default_factory=list)
    user_feedback_log: List[UserFeedbackRecord] = Field(default_factory=list)

    def model_post_init(self, __context: Any) -> None:
        if not self.category_counts and self.entries_by_category:
            self.category_counts = {k: len(v) for k, v in self.entries_by_category.items()}
        if self.total_entries == 0 and self.entries_by_category:
            self.total_entries = sum(len(v) for v in self.entries_by_category.values())
