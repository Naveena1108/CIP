from pydantic import Field, model_validator
from .base import CanonicalSignalBase

class PlacementsSignal(CanonicalSignalBase):
    graduation_year: int = Field(..., ge=2000, le=2100)
    eligible_students: int = Field(..., gt=0, description="Graduating students registered for placement")
    placed_students: int = Field(..., ge=0, description="Students who received offers")
    placement_percentage: float = Field(..., ge=0.0, le=100.0, description="Placement success rate")
    median_salary_lpa: float = Field(..., ge=0.0, description="Median CTC in Lakhs Per Annum")
    max_salary_lpa: float = Field(..., ge=0.0, description="Highest CTC offer in LPA")
    top_tier_recruiters_count: int = Field(default=0, ge=0)
    unplaced_count: int = Field(..., ge=0)

    @model_validator(mode="after")
    def validate_placement_metrics(self):
        if self.placed_students > self.eligible_students:
            raise ValueError("Placed students cannot exceed eligible students")
        expected_unplaced = self.eligible_students - self.placed_students
        if self.unplaced_count != expected_unplaced:
            raise ValueError(f"Unplaced count mismatch: got {self.unplaced_count}, expected {expected_unplaced}")
        if self.max_salary_lpa < self.median_salary_lpa:
            raise ValueError("Max salary cannot be lower than median salary")
        return self
