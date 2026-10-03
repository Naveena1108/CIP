"""
ACT-02: Autoregressive Trajectory Predictor.

Projects the Composite Risk Index (CRI) forward in time using a simple
momentum-based autoregressive model. Supports both STATUS_QUO projection
and WITH_INTERVENTION simulation where external policy levers modify the
underlying feature slopes before projection.

Zero external dependencies beyond Python stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .features import FeatureVector


@dataclass(frozen=True)
class TrajectoryPoint:
    """A single projected data-point on the CRI trajectory."""

    year_offset: int
    projected_cri: float
    confidence_band_low: float
    confidence_band_high: float
    scenario: str  # 'STATUS_QUO' | 'WITH_INTERVENTION'

    def model_dump(self, *args, **kwargs) -> Dict[str, Any]:
        return {
            "year_offset": self.year_offset,
            "projected_cri": self.projected_cri,
            "confidence_band_low": self.confidence_band_low,
            "confidence_band_high": self.confidence_band_high,
            "scenario": self.scenario,
        }


class TrajectoryPredictor:
    """
    Projects institutional CRI trajectories using a simple autoregressive model.

    Model
    -----
    CRI(t+1) = clamp( CRI(t) + momentum_factor * slope_composite, 0.0, 1.0 )

    where *slope_composite* is a weighted combination of per-feature slopes
    derived from the ``feature_slopes`` dictionary.

    Parameters
    ----------
    momentum_factor : float
        Scaling coefficient applied to the composite slope each step.
        Default 0.15 (status-quo projects slowly).
    confidence_spread : float
        Per-year-offset spread for confidence bands (±spread * year_offset).
        Default 0.08.
    """

    _SLOPE_WEIGHTS: Dict[str, float] = {
        "vacancy_rate_slope": 0.40,
        "placement_pct_slope": -0.35,   # negative: falling placement => rising risk
        "closing_rank_slope": 0.25,
        "dynamic_risk_slope": 0.30,     # universal multi-domain risk momentum slope
    }

    def __init__(
        self,
        momentum_factor: float = 0.15,
        confidence_spread: float = 0.08,
    ) -> None:
        self.momentum_factor = momentum_factor
        self.confidence_spread = confidence_spread

    def predict_trajectory(
        self,
        current_cri: float,
        feature_slopes: Dict[str, float],
        years_forward: int = 3,
    ) -> List[TrajectoryPoint]:
        """Project CRI under the *STATUS_QUO* scenario (no intervention)."""
        slope_composite = self._compute_slope_composite(feature_slopes)
        return self._project(current_cri, slope_composite, years_forward, "STATUS_QUO")

    def simulate_intervention(
        self,
        current_cri: float,
        feature_slopes: Dict[str, float],
        intervention_effects: Dict[str, float],
        years_forward: int = 3,
    ) -> List[TrajectoryPoint]:
        """Project CRI under the *WITH_INTERVENTION* scenario."""
        adjusted_slopes = dict(feature_slopes)

        if "vacancy_rate_reduction" in intervention_effects:
            adjusted_slopes["vacancy_rate_slope"] = (
                adjusted_slopes.get("vacancy_rate_slope", 0.0)
                - intervention_effects["vacancy_rate_reduction"]
            )

        if "placement_boost" in intervention_effects:
            adjusted_slopes["placement_pct_slope"] = (
                adjusted_slopes.get("placement_pct_slope", 0.0)
                + intervention_effects["placement_boost"]
            )

        if "closing_rank_stabilization" in intervention_effects:
            adjusted_slopes["closing_rank_slope"] = (
                adjusted_slopes.get("closing_rank_slope", 0.0)
                - intervention_effects["closing_rank_stabilization"]
            )

        if "dynamic_risk_reduction" in intervention_effects:
            adjusted_slopes["dynamic_risk_slope"] = (
                adjusted_slopes.get("dynamic_risk_slope", 0.0)
                - intervention_effects["dynamic_risk_reduction"]
            )

        slope_composite = self._compute_slope_composite(adjusted_slopes)
        return self._project(current_cri, slope_composite, years_forward, "WITH_INTERVENTION")


    def predict_from_feature_vector(
        self,
        current_cri: float,
        feature_vector: "FeatureVector",
        years_forward: int = 3,
    ) -> List[TrajectoryPoint]:
        """Convenience method to project directly from a FeatureVector."""
        slopes = {
            "vacancy_rate_slope": feature_vector.vacancy_rate_slope * 10.0,
            "placement_pct_slope": feature_vector.placement_pct_slope,
            "closing_rank_slope": feature_vector.closing_rank_slope / 1000.0,
        }
        return self.predict_trajectory(current_cri, slopes, years_forward=years_forward)

    def simulate_from_feature_vector(
        self,
        current_cri: float,
        feature_vector: "FeatureVector",
        intervention_effects: Dict[str, float],
        years_forward: int = 3,
    ) -> List[TrajectoryPoint]:
        """Convenience method to simulate intervention directly from a FeatureVector."""
        slopes = {
            "vacancy_rate_slope": feature_vector.vacancy_rate_slope * 10.0,
            "placement_pct_slope": feature_vector.placement_pct_slope,
            "closing_rank_slope": feature_vector.closing_rank_slope / 1000.0,
        }
        return self.simulate_intervention(current_cri, slopes, intervention_effects, years_forward=years_forward)

    def _compute_slope_composite(self, feature_slopes: Dict[str, float]) -> float:
        """Weighted aggregation of individual feature slopes."""
        composite = 0.0
        for key, weight in self._SLOPE_WEIGHTS.items():
            composite += weight * feature_slopes.get(key, 0.0)
        return composite

    @staticmethod
    def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
        return max(lo, min(hi, value))

    def _project(
        self,
        current_cri: float,
        slope_composite: float,
        years_forward: int,
        scenario: str,
    ) -> List[TrajectoryPoint]:
        """Run the autoregressive loop and build trajectory points."""
        points: List[TrajectoryPoint] = []
        cri = current_cri

        for offset in range(1, years_forward + 1):
            cri = self._clamp(cri + self.momentum_factor * slope_composite)
            band = self.confidence_spread * offset
            points.append(
                TrajectoryPoint(
                    year_offset=offset,
                    projected_cri=round(cri, 6),
                    confidence_band_low=round(self._clamp(cri - band), 6),
                    confidence_band_high=round(self._clamp(cri + band), 6),
                    scenario=scenario,
                )
            )

        return points
