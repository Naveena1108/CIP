from typing import Optional
from pydantic import Field
from .base import CanonicalSignalBase

class CETRankingSignal(CanonicalSignalBase):
    quota_category: str = Field(default="General", description="Admission quota category")
    opening_rank: int = Field(..., ge=1, description="Opening CET rank")
    closing_rank: int = Field(..., ge=1, description="Closing CET cutoff rank")
    percentile_cutoff: float = Field(..., ge=0.0, le=100.0, description="Cutoff percentile")
    state_rank: Optional[int] = Field(default=None, ge=1)
    national_rank: Optional[int] = Field(default=None, ge=1)
