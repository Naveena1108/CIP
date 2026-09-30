from typing import List, Literal, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from .base import ProvenanceMetadata

class SignalAnomaly(BaseModel):
    signal_name: str
    academic_year: int
    metric_name: str
    observed_value: float
    baseline_value: float
    deviation_zscore: float
    severity: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    description: str

class CrisisAssessment(BaseModel):
    institution_id: str
    organization_id: Optional[str] = None
    assessment_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    composite_risk_index: float = Field(..., ge=0.0, le=1.0, description="Composite Risk Index (0.0 to 1.0)")
    risk_level: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    primary_driving_signal: str
    anomalies_detected: List[SignalAnomaly] = Field(default_factory=list)
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    executive_summary: Optional[str] = None
    recommended_mitigations: List[str] = Field(default_factory=list)
    provenance: Optional[ProvenanceMetadata] = None
