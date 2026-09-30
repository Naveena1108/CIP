"""
Feature Engineering & Temporal Windowing Module.

Extracts temporal features from canonical signal histories:
- Rate-of-change slopes
- Rolling window averages
- Lead-lag cohort pairings
- Signal divergence ratios

This module contains ZERO I/O. It operates exclusively on typed
CanonicalSignalBase subclass instances.
"""

from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
import math

from src.contracts import (
    CETRankingSignal,
    AdmissionsSignal,
    PlacementsSignal,
)


@dataclass(frozen=True)
class FeatureVector:
    """Immutable feature vector for a single department at a point in time."""
    institution_id: str
    department: str
    academic_year: int

    # Admissions features
    vacancy_rate_current: float
    vacancy_rate_slope: float          # per-year change in vacancy rate
    enrollment_delta_pct: float        # % change from prior year

    # Placement features
    placement_pct_current: float
    placement_pct_slope: float         # per-year change in placement %
    unplaced_ratio_current: float

    # CET / Ranking features
    closing_rank_current: float
    closing_rank_slope: float          # per-year change in closing rank
    percentile_delta: float            # change in percentile cutoff

    # Cross-signal divergence
    intake_to_placed_divergence: float  # ratio of intake growth to placed growth
    sparse_flag: bool                   # True if <3 years of history


def _linear_slope(values: List[float]) -> float:
    """Compute ordinary least-squares slope over equally-spaced observations."""
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    den = sum((i - x_mean) ** 2 for i in range(n))
    if den == 0:
        return 0.0
    return num / den


def _pct_change(old: float, new: float) -> float:
    """Percentage change from old to new, safe for zero baseline."""
    if old == 0:
        return 0.0 if new == 0 else 100.0
    return ((new - old) / abs(old)) * 100.0


def extract_department_features(
    cet_history: List[CETRankingSignal],
    admissions_history: List[AdmissionsSignal],
    placements_history: List[PlacementsSignal],
) -> Optional[FeatureVector]:
    """
    Extract a FeatureVector for a single department from its temporal signal history.

    All three histories must belong to the same institution and department.
    Returns None if no data is available.
    """
    if not admissions_history:
        return None

    sorted_adm = sorted(admissions_history, key=lambda s: s.academic_year)
    sorted_plc = sorted(placements_history, key=lambda s: s.graduation_year) if placements_history else []
    sorted_cet = sorted(cet_history, key=lambda s: s.academic_year) if cet_history else []

    latest_adm = sorted_adm[-1]
    institution_id = latest_adm.institution_id
    department = latest_adm.department
    academic_year = latest_adm.academic_year
    sparse = len(sorted_adm) < 3

    # --- Admissions features ---
    vacancy_rates = [s.vacancy_rate for s in sorted_adm]
    vacancy_rate_current = vacancy_rates[-1]
    vacancy_rate_slope = _linear_slope(vacancy_rates)

    enrollments = [float(s.enrolled_count) for s in sorted_adm]
    enrollment_delta_pct = _pct_change(enrollments[-2], enrollments[-1]) if len(enrollments) >= 2 else 0.0

    # --- Placement features ---
    if sorted_plc:
        latest_plc = sorted_plc[-1]
        placement_pcts = [s.placement_percentage for s in sorted_plc]
        placement_pct_current = placement_pcts[-1]
        placement_pct_slope = _linear_slope(placement_pcts)
        unplaced_ratio = float(latest_plc.unplaced_count) / float(latest_plc.eligible_students) if latest_plc.eligible_students > 0 else 0.0
    else:
        placement_pct_current = 0.0
        placement_pct_slope = 0.0
        unplaced_ratio = 1.0

    # --- CET / Ranking features ---
    if sorted_cet:
        latest_cet = sorted_cet[-1]
        closing_ranks = [float(s.closing_rank) for s in sorted_cet]
        closing_rank_current = closing_ranks[-1]
        closing_rank_slope = _linear_slope(closing_ranks)
        percentiles = [s.percentile_cutoff for s in sorted_cet]
        percentile_delta = percentiles[-1] - percentiles[0] if len(percentiles) >= 2 else 0.0
    else:
        closing_rank_current = 0.0
        closing_rank_slope = 0.0
        percentile_delta = 0.0

    # --- Cross-signal divergence ---
    if len(enrollments) >= 2 and sorted_plc and len(sorted_plc) >= 2:
        intake_growth = _pct_change(enrollments[0], enrollments[-1])
        placed_counts = [float(s.placed_students) for s in sorted_plc]
        placed_growth = _pct_change(placed_counts[0], placed_counts[-1])
        if placed_growth == 0:
            divergence = intake_growth / 100.0 if intake_growth != 0 else 0.0
        else:
            divergence = (intake_growth - placed_growth) / 100.0
    else:
        divergence = 0.0

    return FeatureVector(
        institution_id=institution_id,
        department=department,
        academic_year=academic_year,
        vacancy_rate_current=round(vacancy_rate_current, 4),
        vacancy_rate_slope=round(vacancy_rate_slope, 6),
        enrollment_delta_pct=round(enrollment_delta_pct, 2),
        placement_pct_current=round(placement_pct_current, 2),
        placement_pct_slope=round(placement_pct_slope, 4),
        unplaced_ratio_current=round(unplaced_ratio, 4),
        closing_rank_current=closing_rank_current,
        closing_rank_slope=round(closing_rank_slope, 2),
        percentile_delta=round(percentile_delta, 2),
        intake_to_placed_divergence=round(divergence, 4),
        sparse_flag=sparse,
    )


def extract_institutional_features(
    cet_history: List[CETRankingSignal],
    admissions_history: List[AdmissionsSignal],
    placements_history: List[PlacementsSignal],
) -> Dict[str, FeatureVector]:
    """
    Extract FeatureVectors for every department in an institution's signal history.

    Returns a dict keyed by department name.
    """
    departments = sorted(set(s.department for s in admissions_history))
    results: Dict[str, FeatureVector] = {}

    for dept in departments:
        d_cet = [s for s in cet_history if s.department == dept]
        d_adm = [s for s in admissions_history if s.department == dept]
        d_plc = [s for s in placements_history if s.department == dept]

        fv = extract_department_features(d_cet, d_adm, d_plc)
        if fv is not None:
            results[dept] = fv

    return results
