"""
Contracts for CIP Phase 3: Institutional Intelligence.

Enforces explicit epistemic separation:
OBSERVED_FACT -> ANALYSIS -> INFERENCE -> PREDICTION -> RECOMMENDATION
plus UNKNOWN_INSUFFICIENT_EVIDENCE.

Defines structured models for:
- Institution-specific Historical Baselines (never fabricated from insufficient history)
- Multi-dimensional Evaluated Anomalies (magnitude, historical deviation, persistence,
  coverage, materiality, cross-signal confirmation, and 6-part explanation)
- 5-Stage Risk Progression (Observation -> Anomaly -> Emerging Risk -> Institutional Risk -> Crisis)
- Cross-Signal Intelligence (distinguishing correlation from causation via 'potential contributing factor')
- Investigation Objects (finding, evidence, related_signals, potential_contributing_factors,
  alternative_explanations, confidence, missing_information)
- Early Warning Detection (weak_signal -> repeated_anomaly -> cross_signal_confirmation -> emerging_risk)
- What-Changed State Delta Analysis (new_signals, worsening_signals, improving_signals,
  resolved_risks, new_risks, changed_relationships, changed_forecasts)
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


EpistemicType = Literal[
    "OBSERVED_FACT",
    "ANALYSIS",
    "INFERENCE",
    "PREDICTION",
    "RECOMMENDATION",
    "UNKNOWN_INSUFFICIENT_EVIDENCE",
]

BaselineStatus = Literal[
    "ESTABLISHED",
    "TWO_PERIOD_COMPARISON_ONLY",
    "INSUFFICIENT_HISTORY",
    "CONTRADICTORY_HISTORY",
]

RiskStage = Literal[
    "Observation",
    "Anomaly",
    "Emerging Risk",
    "Institutional Risk",
    "Crisis",
]

EarlyWarningStage = Literal[
    "weak_signal",
    "repeated_anomaly",
    "cross_signal_confirmation",
    "emerging_risk",
]


class EpistemicStatement(BaseModel):
    """Atomic intelligence statement tagged with its strict epistemic layer."""
    statement_id: str
    epistemic_type: EpistemicType
    domain: str
    metric_name: Optional[str] = None
    department: Optional[str] = None
    time_period: Optional[str] = None
    statement: str
    comparison_or_basis: Optional[str] = None
    evidence_refs: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)


class EpistemicIntelligenceModel(BaseModel):
    """
    Explicit separation of institutional intelligence into:
    OBSERVED FACT -> ANALYSIS -> INFERENCE -> PREDICTION -> RECOMMENDATION
    plus UNKNOWN / INSUFFICIENT EVIDENCE.
    """
    observed_facts: List[EpistemicStatement] = Field(default_factory=list)
    analyses: List[EpistemicStatement] = Field(default_factory=list)
    inferences: List[EpistemicStatement] = Field(default_factory=list)
    predictions: List[EpistemicStatement] = Field(default_factory=list)
    recommendations: List[EpistemicStatement] = Field(default_factory=list)
    unknown_and_insufficient_evidence: List[EpistemicStatement] = Field(default_factory=list)


class HistoricalBaseline(BaseModel):
    """
    Institution-specific historical baseline for a metric.
    Never fabricates a baseline when insufficient historical periods exist.
    """
    institution_id: str
    domain: str
    metric_name: str
    metric_label: str
    department: Optional[str] = None
    program: Optional[str] = None
    unit: Optional[str] = None
    polarity: str = "neutral"
    baseline_status: BaselineStatus
    periods_available: List[int] = Field(default_factory=list)
    historical_periods_used: List[int] = Field(default_factory=list)
    baseline_mean: Optional[float] = None
    baseline_std: Optional[float] = None
    baseline_median: Optional[float] = None
    baseline_min: Optional[float] = None
    baseline_max: Optional[float] = None
    trend_slope_per_period: Optional[float] = None
    latest_period: Optional[int] = None
    latest_value: Optional[float] = None
    prior_period: Optional[int] = None
    prior_value: Optional[float] = None
    has_contradictions: bool = False
    explanation: str


class EvaluatedAnomaly(BaseModel):
    """
    Multi-dimensional anomaly evaluation incorporating magnitude, historical deviation,
    persistence, coverage, materiality, and cross-signal confirmation, with explicit
    6-part human-verifiable explanation.
    """
    anomaly_id: str
    domain: str
    metric_name: str
    metric_label: str
    department: Optional[str] = None
    academic_year: Optional[int] = None
    observed_value: float
    comparison_value: Optional[float] = None
    unit: Optional[str] = None
    polarity: str = "neutral"

    # 6 Evaluation Dimensions
    magnitude_score: float = Field(ge=0.0, le=1.0)
    absolute_delta: Optional[float] = None
    relative_pct_change: Optional[float] = None
    historical_deviation_zscore: Optional[float] = None
    historical_deviation_score: float = Field(ge=0.0, le=1.0)
    persistence_periods: int = 1
    persistence_score: float = Field(ge=0.0, le=1.0)
    coverage_ratio: float = Field(ge=0.0, le=1.0)
    coverage_scope: str
    materiality_score: float = Field(ge=0.0, le=1.0)
    cross_signal_confirmation: bool = False
    confirming_signals: List[str] = Field(default_factory=list)

    composite_significance_score: float = Field(ge=0.0, le=1.0)
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    risk_stage: RiskStage

    # 6 Required Explanation Fields
    what_changed: str
    by_how_much: str
    when: str
    comparison_basis: str
    significance: str
    evidence: List[str] = Field(default_factory=list)


class RiskProgressionItem(BaseModel):
    """A finding positioned along the 5-stage risk progression ladder."""
    item_id: str
    stage: RiskStage
    domain: str
    metric_name: str
    department: Optional[str] = None
    time_period: Optional[str] = None
    summary: str
    progression_rationale: str
    significance_score: float = Field(ge=0.0, le=1.0)
    evidence_refs: List[str] = Field(default_factory=list)


class RiskProgressionLadder(BaseModel):
    """
    5-stage risk progression model:
    Observation -> Anomaly -> Emerging Risk -> Institutional Risk -> Crisis.
    Prevents labeling isolated or single-period anomalies as crises.
    """
    overall_institutional_stage: RiskStage
    composite_risk_index: float = Field(ge=0.0, le=1.0)
    legacy_risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    observations: List[RiskProgressionItem] = Field(default_factory=list)
    anomalies: List[RiskProgressionItem] = Field(default_factory=list)
    emerging_risks: List[RiskProgressionItem] = Field(default_factory=list)
    institutional_risks: List[RiskProgressionItem] = Field(default_factory=list)
    crises: List[RiskProgressionItem] = Field(default_factory=list)
    stage_counts: Dict[str, int] = Field(default_factory=dict)
    stage_rationale: str


class RelatedSignalInspection(BaseModel):
    """Inspection result of a related signal when a primary signal changes significantly."""
    domain: str
    metric_name: str
    metric_label: str
    department: Optional[str] = None
    time_period: Optional[str] = None
    observed_value: Optional[float] = None
    prior_or_baseline_value: Optional[float] = None
    relative_pct_change: Optional[float] = None
    relationship_status: Literal[
        "CORROBORATING_CHANGE",
        "STABLE_NO_CHANGE",
        "DIVERGENT_TREND",
        "CONTRADICTORY_SOURCE",
    ]
    summary: str
    evidence_refs: List[str] = Field(default_factory=list)


class CrossSignalFinding(BaseModel):
    """
    Cross-signal intelligence analysis triggered by a significant signal change.
    Distinguishes correlation from causation using 'potential contributing factor'.
    """
    primary_domain: str
    primary_metric: str
    department: Optional[str] = None
    trigger_summary: str
    inspected_related_signals: List[RelatedSignalInspection] = Field(default_factory=list)
    corroborating_domains: List[str] = Field(default_factory=list)
    uninspected_missing_domains: List[str] = Field(default_factory=list)
    causal_evidence_present: bool = False
    correlation_vs_causation_note: str
    potential_contributing_factors: List[str] = Field(default_factory=list)


class InvestigationObject(BaseModel):
    """
    Structured investigation dossier for a major institutional finding.
    Provides all 7 required investigation components.
    """
    investigation_id: str
    domain: str
    metric_name: str
    department: Optional[str] = None
    risk_stage: RiskStage
    finding: str
    evidence: List[str] = Field(default_factory=list)
    related_signals: List[RelatedSignalInspection] = Field(default_factory=list)
    potential_contributing_factors: List[str] = Field(default_factory=list)
    alternative_explanations: List[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_rationale: str
    missing_information: List[str] = Field(default_factory=list)


class EarlyWarningIndicator(BaseModel):
    """
    Tracks progression along the early-warning chain:
    weak_signal -> repeated_anomaly -> cross_signal_confirmation -> emerging_risk.
    """
    warning_id: str
    current_stage: EarlyWarningStage
    stage_index: int = Field(ge=1, le=4, description="1=weak_signal, 2=repeated_anomaly, 3=cross_signal_confirmation, 4=emerging_risk")
    domain: str
    metric_name: str
    department: Optional[str] = None
    latest_period: Optional[str] = None
    summary: str
    progression_chain: List[str] = Field(default_factory=list)
    confirming_related_signals: List[str] = Field(default_factory=list)
    escalation_triggers: List[str] = Field(default_factory=list)
    evidence_refs: List[str] = Field(default_factory=list)


class SignalChangeDelta(BaseModel):
    """Describes a newly added, worsening, or improving signal between previous and current state."""
    domain: str
    metric_name: str
    metric_label: str
    department: Optional[str] = None
    previous_period: Optional[str] = None
    current_period: Optional[str] = None
    previous_value: Optional[float] = None
    current_value: Optional[float] = None
    absolute_delta: Optional[float] = None
    relative_pct_change: Optional[float] = None
    direction: Literal["NEW", "WORSENING", "IMPROVING", "UNCHANGED"]
    summary: str
    evidence_refs: List[str] = Field(default_factory=list)


class RiskChangeDelta(BaseModel):
    """Describes a newly emerged risk or a resolved risk between previous and current state."""
    domain: str
    metric_name: str
    department: Optional[str] = None
    change_type: Literal["NEW_RISK", "RESOLVED_RISK", "ESCALATED_RISK", "DEESCALATED_RISK"]
    previous_stage: Optional[str] = None
    current_stage: Optional[str] = None
    summary: str


class RelationshipChangeDelta(BaseModel):
    """Describes how relationships/couplings between institutional signals changed over time."""
    domain_a: str
    metric_a: str
    domain_b: str
    metric_b: str
    department: Optional[str] = None
    previous_relationship: str
    current_relationship: str
    summary: str


class ForecastChangeDelta(BaseModel):
    """Describes how the 3-year trajectory projection shifted compared to the prior state/period."""
    metric_or_index: str
    previous_current_cri: float
    new_current_cri: float
    previous_year3_projected_cri: float
    new_year3_projected_cri: float
    projected_delta: float
    direction: Literal["WORSENED_OUTLOOK", "IMPROVED_OUTLOOK", "STABLE_OUTLOOK"]
    summary: str


class WhatChangedReport(BaseModel):
    """
    Comprehensive state comparison report detailing:
    new_signals, worsening_signals, improving_signals, resolved_risks,
    new_risks, changed_relationships, and changed_forecasts.
    """
    comparison_available: bool
    comparison_mode: str
    previous_state_label: str
    current_state_label: str
    new_signals: List[SignalChangeDelta] = Field(default_factory=list)
    worsening_signals: List[SignalChangeDelta] = Field(default_factory=list)
    improving_signals: List[SignalChangeDelta] = Field(default_factory=list)
    resolved_risks: List[RiskChangeDelta] = Field(default_factory=list)
    new_risks: List[RiskChangeDelta] = Field(default_factory=list)
    changed_relationships: List[RelationshipChangeDelta] = Field(default_factory=list)
    changed_forecasts: List[ForecastChangeDelta] = Field(default_factory=list)
    summary: str


class InstitutionalIntelligenceReport(BaseModel):
    """
    Top-level CIP Phase 3 Institutional Intelligence output.
    Unifies epistemic separation, institution-specific baselines, multi-dimensional anomalies,
    5-stage risk progression, cross-signal intelligence, investigation objects,
    early-warning progression, and what-changed state delta analysis.
    """
    institution_id: str
    organization_id: Optional[str] = None
    view_type: str = "INSTITUTION_VIEW"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    epistemic_model: EpistemicIntelligenceModel
    baselines: List[HistoricalBaseline] = Field(default_factory=list)
    evaluated_anomalies: List[EvaluatedAnomaly] = Field(default_factory=list)
    risk_progression: RiskProgressionLadder
    cross_signal_findings: List[CrossSignalFinding] = Field(default_factory=list)
    investigations: List[InvestigationObject] = Field(default_factory=list)
    early_warnings: List[EarlyWarningIndicator] = Field(default_factory=list)
    what_changed: WhatChangedReport
    contradictions_surfaced: List[Dict[str, Any]] = Field(default_factory=list)
