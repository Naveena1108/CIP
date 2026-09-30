"""
Universal Institutional Data Ingestion Contracts for CIP Phase 2.
Covers multi-format extraction, dynamic signal discovery across 16+ institutional domains,
evidence-backed institutional context association, rich provenance, and data quality /
contradiction tracking.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


SIGNAL_DOMAINS = (
    "students",
    "academics",
    "faculty",
    "research",
    "infrastructure",
    "finance",
    "admissions",
    "retention",
    "attendance",
    "grievances",
    "placements",
    "internships",
    "industry",
    "accreditation",
    "compliance",
    "feedback",
    "ranking",
    "general_institutional",
)

DataQualityIssueType = Literal[
    "missing_periods",
    "incomplete_coverage",
    "ocr_uncertainty",
    "duplicates",
    "outdated_data",
    "ambiguous_values",
    "contradictory_sources",
]


class SignalProvenance(BaseModel):
    """
    Fine-grained provenance for every extracted institutional signal.
    Retains source, document, page/section, sheet/row/cell, excerpt/reference,
    and extraction confidence.
    """
    source: str = Field(..., description="Origin source identifier or upload channel")
    document: str = Field(..., description="Document filename or title")
    format_type: str = Field(..., description="Detected file format (PDF, XLSX, CSV, DOCX, PPTX, TXT, IMAGE, AUDIO_VIDEO)")
    page_or_section: Optional[str] = Field(default=None, description="Page number, slide number, or document section heading where available")
    spreadsheet_location: Optional[str] = Field(default=None, description="Sheet, row, and cell coordinate where applicable (e.g., Sheet1!R4C2)")
    excerpt_or_reference: str = Field(..., description="Verbatim text excerpt, table row, or image/media reference supporting the signal")
    extraction_confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score of extraction (0.0 to 1.0)")


class InstitutionalContextAssociation(BaseModel):
    """
    Evidence-grounded institutional context for a signal.
    Fields are populated ONLY when evidence supports them; missing context is never invented.
    """
    organization_id: Optional[str] = Field(default=None, description="Associated organization ID if evidenced or scoped by authenticated upload")
    organization_name: Optional[str] = Field(default=None, description="Organization name if explicitly evidenced")
    institution_id: Optional[str] = Field(default=None, description="Associated institution ID if evidenced or scoped by authenticated upload")
    institution_name: Optional[str] = Field(default=None, description="Institution name if explicitly evidenced in source")
    department: Optional[str] = Field(default=None, description="Department code/name ONLY if evidenced in source; never invented")
    program: Optional[str] = Field(default=None, description="Academic program ONLY if evidenced in source; never invented")
    time_period: Optional[str] = Field(default=None, description="Time period string (e.g., '2023-24', '2024') ONLY if evidenced in source")
    academic_year: Optional[int] = Field(default=None, description="Normalized academic year integer ONLY if evidenced in source")
    source: str = Field(..., description="Source document or system identifier")
    context_evidence: Dict[str, str] = Field(
        default_factory=dict,
        description="Maps each populated context attribute to the exact source evidence that established it",
    )


class DiscoveredSignal(BaseModel):
    """
    Dynamically discovered institutional signal across any operational or academic domain.
    """
    signal_id: str = Field(..., description="Unique signal observation identifier")
    domain: str = Field(..., description="Institutional domain (students, faculty, finance, research, etc.)")
    metric_name: str = Field(..., description="Canonical/normalized metric key")
    metric_label: str = Field(..., description="Original human-readable metric label from source")
    value: Optional[float] = Field(default=None, description="Numeric value if quantitative")
    raw_value: str = Field(..., description="Verbatim value string from source")
    unit: Optional[str] = Field(default=None, description="Unit of measurement (%, count, INR_Lakhs, rank, ratio, grade, etc.)")
    polarity: Literal["higher_is_better", "lower_is_better", "neutral"] = Field(default="neutral")
    risk_contribution: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Normalized threat indicator (0.0=healthy, 1.0=critical)")
    is_contradictory: bool = Field(default=False, description="True if another signal for the same entity/metric/period has a conflicting value")
    contradiction_group_id: Optional[str] = Field(default=None, description="Shared identifier linking contradictory observations so all remain visible")
    is_duplicate: bool = Field(default=False, description="True if an identical observation was already recorded")
    context: InstitutionalContextAssociation
    provenance: SignalProvenance
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DataQualityIssue(BaseModel):
    """
    Detected data quality issue, ambiguity, OCR uncertainty, duplicate, missing period, or contradiction.
    """
    issue_type: DataQualityIssueType
    severity: Literal["HIGH", "MEDIUM", "LOW"]
    description: str
    affected_domain: Optional[str] = None
    affected_metric: Optional[str] = None
    affected_department: Optional[str] = None
    affected_period: Optional[str] = None
    conflicting_values: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="When issue_type is 'contradictory_sources', preserves all conflicting values and their provenance side by side",
    )
    provenance_refs: List[str] = Field(default_factory=list)


class UniversalIngestionResult(BaseModel):
    """
    Complete output of the CIP Phase 2 Universal Institutional Data Ingestion pipeline.
    """
    ingestion_id: str
    status: Literal["SUCCESS", "PARTIAL_EXTRACTION", "UNSUPPORTED_FORMAT", "EXTRACTION_FAILED"]
    truthful_explanation: str
    detected_format: str
    mime_type: str
    filename: str
    organization_id: Optional[str] = None
    institution_id: Optional[str] = None
    pipeline_stages: Dict[str, str] = Field(default_factory=dict)
    discovered_signals: List[DiscoveredSignal] = Field(default_factory=list)
    domains_discovered: List[str] = Field(default_factory=list)
    canonical_signals_mapped: Dict[str, int] = Field(default_factory=dict)
    data_quality_issues: List[DataQualityIssue] = Field(default_factory=list)
    contradictions_detected: int = 0
    duplicates_detected: int = 0
    total_signals_ingested: int = 0
    admissions_count: int = 0
    placements_count: int = 0
    cet_ranking_count: int = 0
    extraction_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
