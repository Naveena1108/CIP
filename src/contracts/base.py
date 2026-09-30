from datetime import datetime, timezone
from typing import Literal, Optional
from pydantic import BaseModel, Field, ConfigDict

class ProvenanceMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)
    source_id: str = Field(..., description="Identifier for the source system or dataset")
    source_type: Literal["real_verified", "synthetic_generated", "institutional_export", "mock"]
    ingestion_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version: str = Field(default="1.0.0")
    hash_checksum: Optional[str] = None

class CanonicalSignalBase(BaseModel):
    institution_id: str = Field(..., min_length=1, description="Unique institutional identifier")
    academic_year: int = Field(..., ge=2000, le=2100, description="Academic year of observation")
    department: str = Field(..., min_length=1, description="Academic department or program")
    provenance: Optional[ProvenanceMetadata] = None
