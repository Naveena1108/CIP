from pydantic import Field, model_validator
from .base import CanonicalSignalBase

class AdmissionsSignal(CanonicalSignalBase):
    sanctioned_intake: int = Field(..., gt=0, description="Approved seat capacity")
    enrolled_count: int = Field(..., ge=0, description="Actual students admitted")
    vacancy_count: int = Field(..., ge=0, description="Unfilled seats")
    vacancy_rate: float = Field(..., ge=0.0, le=1.0, description="Ratio of vacant seats to intake")
    gender_diversity_ratio: float = Field(default=0.0, ge=0.0, description="Female to total enrollment ratio")
    dropouts_year_1: int = Field(default=0, ge=0, description="First-year dropout count")

    @model_validator(mode="after")
    def validate_capacity_and_vacancy(self):
        if self.enrolled_count > self.sanctioned_intake:
            raise ValueError(f"Enrolled count ({self.enrolled_count}) cannot exceed sanctioned intake ({self.sanctioned_intake})")
        expected_vacancy = self.sanctioned_intake - self.enrolled_count
        if self.vacancy_count != expected_vacancy:
            raise ValueError(f"Vacancy count mismatch: got {self.vacancy_count}, expected {expected_vacancy}")
        expected_rate = round(float(self.vacancy_count) / float(self.sanctioned_intake), 4)
        if abs(self.vacancy_rate - expected_rate) > 0.01:
            raise ValueError(f"Vacancy rate mismatch: got {self.vacancy_rate}, expected {expected_rate}")
        return self
