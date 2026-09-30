"""
CIP Phase 4 Engine: Explainable Forecasting, What-If Analysis, and Institutional Memory.

Strictly deterministic mathematical implementation:
- Never fabricates a forecast when historical evidence is insufficient (< 2 periods).
- Never allows an LLM to invent forecast values, methodology, or change attributions.
- Every What-If control maps 1-to-1 to a real mathematical model input in TrajectoryPredictor.
- Persists and organizes 7 distinct institutional memory categories:
  observed_fact, analysis, inference, prediction, outcome, user_feedback, unknown.
- Implements longitudinal learning loop:
  Prediction -> later observation -> outcome comparison -> prediction accuracy -> institutional learning.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.contracts import (
    AdmissionsSignal,
    CETRankingSignal,
    PlacementsSignal,
    DiscoveredSignal,
    CrisisAssessment,
    InstitutionalIntelligenceReport,
    EpistemicType,
    ForecastStatus,
    MemoryCategory,
    FeedbackVerdict,
    TrajectoryDriver,
    ForecastDataCoverage,
    ForecastTrajectoryPoint,
    ChangedForecastInput,
    ForecastChangeExplanation,
    ExplainableForecast,
    WhatIfControlMapping,
    WhatIfBaselineSummary,
    WhatIfInterventionSpec,
    WhatIfRiskDelta,
    WhatIfAnalysisResponse,
    InstitutionalMemoryEntry,
    PredictionOutcomeComparison,
    UserFeedbackRecord,
    HypothesisRecord,
    InstitutionalMemoryStoreView,
)
from src.engine.crisis_scorer import CrisisIntelligenceEngine
from src.engine.features import extract_institutional_features
from src.engine.predictor import TrajectoryPredictor


SUPPORTED_WHAT_IF_CONTROLS: Dict[str, Tuple[str, float, str]] = {
    "vacancy_rate_reduction": (
        "vacancy_rate_slope",
        -1.0,
        "Directly subtracts from vacancy_rate_slope (weight +0.40 in composite momentum).",
    ),
    "placement_boost": (
        "placement_pct_slope",
        1.0,
        "Directly adds to placement_pct_slope (weight -0.35 in composite momentum; higher placement reduces CRI).",
    ),
    "closing_rank_stabilization": (
        "closing_rank_slope",
        -1.0,
        "Directly subtracts from closing_rank_slope (weight +0.25 in composite momentum).",
    ),
    "dynamic_risk_reduction": (
        "dynamic_risk_slope",
        -1.0,
        "Directly subtracts from dynamic_risk_slope (weight +0.30 in composite momentum).",
    ),
}


class ExplainableForecastingAndMemoryEngine:
    """
    Deterministic engine for CIP Phase 4 Explainable Forecasting, What-If Analysis,
    Longitudinal Outcome Comparison, and Institutional Memory assembly.
    """

    MINIMUM_PERIODS_FOR_FORECAST = 2

    def __init__(
        self,
        momentum_factor: float = 0.15,
        confidence_spread: float = 0.08,
    ) -> None:
        self.predictor = TrajectoryPredictor(
            momentum_factor=momentum_factor,
            confidence_spread=confidence_spread,
        )
        self.crisis_engine = CrisisIntelligenceEngine()

    @staticmethod
    def compute_slopes_and_coverage(
        cet_history: List[CETRankingSignal],
        admissions_history: List[AdmissionsSignal],
        placements_history: List[PlacementsSignal],
        dynamic_signals: Optional[List[DiscoveredSignal]] = None,
    ) -> Tuple[Dict[str, float], ForecastDataCoverage, List[str]]:
        """
        Compute institutional feature slopes, data coverage audit, and historical evidence list
        from canonical and dynamic signals.
        """
        dynamic_signals = dynamic_signals or []
        years_set = set()
        domains_set = set()
        evidence_lines: List[str] = []

        for c in cet_history:
            years_set.add(c.academic_year)
            domains_set.add("admissions")
        for a in admissions_history:
            years_set.add(a.academic_year)
            domains_set.add("admissions")
        for p in placements_history:
            years_set.add(p.academic_year)
            domains_set.add("placements")
        for d in dynamic_signals:
            if d.context.academic_year is not None:
                years_set.add(d.context.academic_year)
            domains_set.add(d.domain)

        years_sorted = sorted(years_set)
        distinct_periods = len(years_sorted)
        total_obs = len(cet_history) + len(admissions_history) + len(placements_history) + len(dynamic_signals)

        is_sufficient = distinct_periods >= ExplainableForecastingAndMemoryEngine.MINIMUM_PERIODS_FOR_FORECAST
        coverage_ratio = round(min(1.0, distinct_periods / 3.0), 4)

        coverage = ForecastDataCoverage(
            distinct_periods=distinct_periods,
            years_covered=years_sorted,
            domains_covered=sorted(domains_set),
            total_observations=total_obs,
            minimum_required_periods=ExplainableForecastingAndMemoryEngine.MINIMUM_PERIODS_FOR_FORECAST,
            coverage_ratio=coverage_ratio,
            is_sufficient=is_sufficient,
        )

        dept_features = extract_institutional_features(cet_history, admissions_history, placements_history)
        total_intake = 0
        vac_slope_sum = 0.0
        plc_slope_sum = 0.0
        rnk_slope_sum = 0.0

        for dept, fv in dept_features.items():
            dept_adm = [a for a in admissions_history if a.department == dept]
            intake = dept_adm[-1].sanctioned_intake if dept_adm else 60
            total_intake += intake
            vac_slope_sum += (fv.vacancy_rate_slope * 10.0) * intake
            plc_slope_sum += fv.placement_pct_slope * intake
            rnk_slope_sum += (fv.closing_rank_slope / 1000.0) * intake

        if total_intake > 0:
            slopes = {
                "vacancy_rate_slope": round(vac_slope_sum / total_intake, 6),
                "placement_pct_slope": round(plc_slope_sum / total_intake, 4),
                "closing_rank_slope": round(rnk_slope_sum / total_intake, 4),
            }
        else:
            slopes = {
                "vacancy_rate_slope": 0.0,
                "placement_pct_slope": 0.0,
                "closing_rank_slope": 0.0,
            }

        # Compute dynamic_risk_slope if multi-year dynamic signals exist
        dyn_by_year: Dict[int, List[float]] = {}
        for d in dynamic_signals:
            if d.context.academic_year is not None and d.risk_contribution is not None and not d.is_duplicate:
                dyn_by_year.setdefault(d.context.academic_year, []).append(float(d.risk_contribution))
        if len(dyn_by_year) >= 2:
            dyn_years = sorted(dyn_by_year.keys())
            first_yr, last_yr = dyn_years[0], dyn_years[-1]
            first_avg = sum(dyn_by_year[first_yr]) / len(dyn_by_year[first_yr])
            last_avg = sum(dyn_by_year[last_yr]) / len(dyn_by_year[last_yr])
            span = max(1, last_yr - first_yr)
            slopes["dynamic_risk_slope"] = round((last_avg - first_avg) / span, 6)

        if years_sorted:
            evidence_lines.append(
                f"Historical span covers {distinct_periods} academic period(s): {', '.join(str(y) for y in years_sorted)} "
                f"across {len(domains_set)} institutional domain(s) ({total_obs} total signal observations)."
            )
        if admissions_history:
            adm_by_yr: Dict[int, Tuple[int, int]] = {}
            for a in admissions_history:
                s, f = adm_by_yr.get(a.academic_year, (0, 0))
                adm_by_yr[a.academic_year] = (s + a.sanctioned_intake, f + a.enrolled_count)
            yr_parts = []

            for y in sorted(adm_by_yr.keys()):
                s, f = adm_by_yr[y]
                vac = round((1.0 - f / s) * 100.0, 1) if s > 0 else 0.0
                yr_parts.append(f"{y}: {f}/{s} seats filled ({vac}% vacancy)")
            evidence_lines.append("Admissions history: " + "; ".join(yr_parts) + ".")
        if placements_history:
            plc_by_yr: Dict[int, Tuple[int, int]] = {}
            for p in placements_history:
                e, pl = plc_by_yr.get(p.academic_year, (0, 0))
                plc_by_yr[p.academic_year] = (e + p.eligible_students, pl + p.placed_students)
            yr_parts = []
            for y in sorted(plc_by_yr.keys()):
                e, pl = plc_by_yr[y]
                rate = round((pl / e) * 100.0, 1) if e > 0 else 0.0
                yr_parts.append(f"{y}: {pl}/{e} placed ({rate}%)")
            evidence_lines.append("Placements history: " + "; ".join(yr_parts) + ".")
        if dynamic_signals:
            evidence_lines.append(
                f"Dynamic multi-domain signals ingested: {len(dynamic_signals)} observation(s) across "
                f"{', '.join(sorted({d.domain for d in dynamic_signals}))}."
            )

        return slopes, coverage, evidence_lines

    def generate_explainable_forecast(
        self,
        institution_id: str,
        cet_history: List[CETRankingSignal],
        admissions_history: List[AdmissionsSignal],
        placements_history: List[PlacementsSignal],
        dynamic_signals: Optional[List[DiscoveredSignal]] = None,
        years_forward: int = 3,
        previous_prediction_payload: Optional[Dict[str, Any]] = None,
    ) -> ExplainableForecast:
        """
        Generate an explainable forecast exposing all 10 required CIP Phase 4 attributes.
        If historical coverage has < 2 distinct time periods, returns an explicit
        INSUFFICIENT_EVIDENCE state with prediction=None instead of fabricating a forecast.
        """
        dynamic_signals = dynamic_signals or []
        slopes, coverage, evidence_lines = self.compute_slopes_and_coverage(
            cet_history=cet_history,
            admissions_history=admissions_history,
            placements_history=placements_history,
            dynamic_signals=dynamic_signals,
        )

        if not coverage.is_sufficient:
            reason = (
                f"Insufficient historical evidence for forecasting: found {coverage.distinct_periods} distinct "
                f"time period(s) ({coverage.years_covered}), but at least {coverage.minimum_required_periods} "
                f"distinct historical periods are required to compute empirical rate-of-change slopes."
            )
            return ExplainableForecast(
                institution_id=institution_id,
                status=ForecastStatus.INSUFFICIENT_EVIDENCE,
                insufficient_evidence_reason=reason,
                prediction=None,
                current_cri=None,
                horizon=f"{years_forward} years forward (blocked: insufficient history)",
                years_forward=years_forward,
                method_actually_used="NONE_INSUFFICIENT_EVIDENCE",
                input_signals={
                    "distinct_periods_observed": coverage.distinct_periods,
                    "years_covered": coverage.years_covered,
                    "domains_covered": coverage.domains_covered,
                    "total_observations": coverage.total_observations,
                },
                historical_evidence=evidence_lines or [
                    "Fewer than 2 historical periods have been ingested for this institution."
                ],
                data_coverage=coverage,
                confidence=0.0,
                limitations=[
                    reason,
                    "CIP never fabricates a precise multi-year trajectory from a single snapshot or empty dataset.",
                    "Ingest data from at least 2 distinct academic years or reporting periods to unlock trajectory forecasting.",
                ],
                alternative_explanations=[
                    "Single-period metrics may reflect temporary operational variance rather than a multi-year structural trend."
                ],
                trajectory_drivers=[],
                why_did_prediction_change=ForecastChangeExplanation(
                    institution_id=institution_id,
                    has_previous_prediction=False,
                    summary="No forecast generated because historical evidence is insufficient (< 2 periods).",
                    deterministic_attribution="Forecast requires at least 2 distinct historical periods to establish rate-of-change slopes.",
                ),
            )

        assessment = self.crisis_engine.evaluate_institution(
            institution_id=institution_id,
            cet_history=cet_history,
            admissions_history=admissions_history,
            placements_history=placements_history,
            dynamic_signals=dynamic_signals,
        )
        current_cri = round(assessment.composite_risk_index, 4)
        max_year = max(coverage.years_covered)

        raw_points = self.predictor.predict_trajectory(
            current_cri=current_cri,
            feature_slopes=slopes,
            years_forward=years_forward,
        )
        forecast_points = [
            ForecastTrajectoryPoint(
                year_offset=pt.year_offset,
                target_period=max_year + pt.year_offset,
                projected_cri=round(pt.projected_cri, 4),
                confidence_band_low=round(pt.confidence_band_low, 4),
                confidence_band_high=round(pt.confidence_band_high, 4),
                scenario=pt.scenario,
            )
            for pt in raw_points
        ]

        # Build trajectory drivers
        drivers: List[TrajectoryDriver] = []
        weights = self.predictor._SLOPE_WEIGHTS
        for key, weight in weights.items():
            if key not in slopes and key == "dynamic_risk_slope":
                continue
            val = float(slopes.get(key, 0.0))
            contrib = round(self.predictor.momentum_factor * weight * val, 6)
            if contrib > 0.0005:
                direction = "WORSENING_RISK"
            elif contrib < -0.0005:
                direction = "IMPROVING_RISK"
            else:
                direction = "NEUTRAL"

            drivers.append(
                TrajectoryDriver(
                    signal_or_slope=key,
                    slope_value=round(val, 6),
                    weight=weight,
                    weighted_contribution=contrib,
                    direction=direction,
                    explanation=(
                        f"{key}={val:+.4f} with weight {weight:+.2f} and momentum factor "
                        f"{self.predictor.momentum_factor:.2f} contributes {contrib:+.4f} CRI per year ({direction})."
                    ),
                )
            )

        drivers.sort(key=lambda d: abs(d.weighted_contribution), reverse=True)

        # Compute deterministic forecast confidence
        contradiction_count = sum(1 for d in dynamic_signals if d.is_contradictory)
        period_bonus = min(0.35, (coverage.distinct_periods - 1) * 0.15)
        domain_bonus = min(0.20, len(coverage.domains_covered) * 0.06)
        contradiction_penalty = min(0.20, contradiction_count * 0.05)
        confidence = round(max(0.35, min(0.92, 0.45 + period_bonus + domain_bonus - contradiction_penalty)), 2)

        limitations = [
            "First-order autoregressive momentum assumes current linear feature slopes persist over the forecast horizon without external policy intervention.",
            f"Confidence bands widen by ±{self.predictor.confidence_spread:.2f} per future year offset (reaching ±{self.predictor.confidence_spread * years_forward:.2f} at year +{years_forward}).",
        ]
        if coverage.distinct_periods < 3:
            limitations.append(
                f"Trajectory is calibrated on {coverage.distinct_periods} periods ({coverage.years_covered}); 3+ periods are recommended to separate structural trend from two-point noise."
            )
        if contradiction_count > 0:
            limitations.append(
                f"{contradiction_count} contradictory signal observation(s) were detected across source documents, reducing forecast confidence by {contradiction_penalty:.2f}."
            )

        alternative_explanations = [
            "Observed slope changes may reflect macroeconomic industry hiring cycles or state-level counseling schedule shifts rather than internal institutional degradation.",
            "Department-level intake expansions or new program launches can temporarily elevate vacancy rates before stabilizing in subsequent admission cycles.",
        ]

        why_changed = self.explain_prediction_change(
            institution_id=institution_id,
            cet_history=cet_history,
            admissions_history=admissions_history,
            placements_history=placements_history,
            dynamic_signals=dynamic_signals,
            current_cri=current_cri,
            current_slopes=slopes,
            current_points=forecast_points,
            years_covered=coverage.years_covered,
            years_forward=years_forward,
            previous_prediction_payload=previous_prediction_payload,
        )

        return ExplainableForecast(
            institution_id=institution_id,
            status=ForecastStatus.SUFFICIENT_EVIDENCE,
            insufficient_evidence_reason=None,
            prediction=forecast_points,
            current_cri=current_cri,
            horizon=f"{years_forward} years ({max_year + 1} to {max_year + years_forward})",
            years_forward=years_forward,
            method_actually_used=(
                "Deterministic First-Order Autoregressive Momentum Model: "
                "CRI(t+1) = clamp(CRI(t) + 0.15 * (0.40*vacancy_rate_slope - 0.35*placement_pct_slope "
                "+ 0.25*closing_rank_slope + 0.30*dynamic_risk_slope), 0.0, 1.0)"
            ),
            input_signals={
                "current_cri": current_cri,
                "feature_slopes": slopes,
                "momentum_factor": self.predictor.momentum_factor,
                "confidence_spread_per_year": self.predictor.confidence_spread,
                "years_covered": coverage.years_covered,
                "domains_covered": coverage.domains_covered,
                "total_observations": coverage.total_observations,
            },
            historical_evidence=evidence_lines,
            data_coverage=coverage,
            confidence=confidence,
            limitations=limitations,
            alternative_explanations=alternative_explanations,
            trajectory_drivers=drivers,
            why_did_prediction_change=why_changed,
        )

    def explain_prediction_change(
        self,
        institution_id: str,
        cet_history: List[CETRankingSignal],
        admissions_history: List[AdmissionsSignal],
        placements_history: List[PlacementsSignal],
        dynamic_signals: List[DiscoveredSignal],
        current_cri: float,
        current_slopes: Dict[str, float],
        current_points: List[ForecastTrajectoryPoint],
        years_covered: List[int],
        years_forward: int = 3,
        previous_prediction_payload: Optional[Dict[str, Any]] = None,
    ) -> ForecastChangeExplanation:
        """
        Deterministically answer: "Why did the prediction change?"
        Identifies exact changed inputs (current_cri and each feature slope) and their
        mathematical contribution to the terminal CRI shift.
        """
        curr_terminal = current_points[-1].projected_cri if current_points else current_cri

        prev_cri: Optional[float] = None
        prev_slopes: Optional[Dict[str, float]] = None
        prev_terminal: Optional[float] = None
        prev_years: List[int] = []

        if previous_prediction_payload and previous_prediction_payload.get("current_cri") is not None:
            prev_cri = float(previous_prediction_payload["current_cri"])
            inp = previous_prediction_payload.get("input_signals", {})
            prev_slopes = dict(inp.get("feature_slopes", {}))
            prev_years = list(inp.get("years_covered", []))
            pred_list = previous_prediction_payload.get("prediction") or []
            if pred_list:
                last_pt = pred_list[-1]
                prev_terminal = float(last_pt.get("projected_cri", prev_cri))
        elif len(years_covered) >= 3:
            # Compare forecast built on prior years (excluding latest year) vs full history
            cutoff_year = years_covered[-1]
            prior_cet = [c for c in cet_history if c.academic_year < cutoff_year]
            prior_adm = [a for a in admissions_history if a.academic_year < cutoff_year]
            prior_plc = [p for p in placements_history if p.academic_year < cutoff_year]
            prior_dyn = [
                d for d in dynamic_signals
                if d.context.academic_year is not None and d.context.academic_year < cutoff_year
            ]
            if prior_cet or prior_adm or prior_plc or prior_dyn:
                prior_assess = self.crisis_engine.evaluate_institution(
                    institution_id=institution_id,
                    cet_history=prior_cet,
                    admissions_history=prior_adm,
                    placements_history=prior_plc,
                    dynamic_signals=prior_dyn,
                )
                prev_cri = round(prior_assess.composite_risk_index, 4)
                prev_slopes, prior_cov, _ = self.compute_slopes_and_coverage(
                    prior_cet, prior_adm, prior_plc, prior_dyn
                )
                prev_years = prior_cov.years_covered
                prior_pts = self.predictor.predict_trajectory(
                    current_cri=prev_cri,
                    feature_slopes=prev_slopes,
                    years_forward=years_forward,
                )
                prev_terminal = round(prior_pts[-1].projected_cri, 4) if prior_pts else prev_cri

        if prev_cri is None or prev_slopes is None or prev_terminal is None:
            return ForecastChangeExplanation(
                institution_id=institution_id,
                has_previous_prediction=False,
                previous_current_cri=None,
                new_current_cri=current_cri,
                previous_terminal_cri=None,
                current_terminal_cri=curr_terminal,
                terminal_cri_delta=0.0,
                changed_inputs=[],
                newly_added_periods=[],
                summary="Baseline initial forecast established; no prior forecast snapshot differs from the current state.",
                deterministic_attribution=(
                    f"Initial forecast anchored at current_cri={current_cri:.4f} projecting to "
                    f"terminal CRI={curr_terminal:.4f} over {years_forward} years."
                ),
            )

        changed_inputs: List[ChangedForecastInput] = []
        cri_delta = round(current_cri - prev_cri, 4)
        if abs(cri_delta) > 1e-6:
            changed_inputs.append(
                ChangedForecastInput(
                    input_name="current_cri",
                    previous_value=round(prev_cri, 4),
                    current_value=round(current_cri, 4),
                    delta=cri_delta,
                    impact_on_projected_cri=cri_delta,
                    explanation=(
                        f"Base Composite Risk Index (current_cri) shifted from {prev_cri:.4f} to {current_cri:.4f} "
                        f"(delta {cri_delta:+.4f}), shifting the starting anchor of the trajectory by {cri_delta:+.4f}."
                    ),
                )
            )

        weights = self.predictor._SLOPE_WEIGHTS
        for key, weight in weights.items():
            old_val = float(prev_slopes.get(key, 0.0))
            new_val = float(current_slopes.get(key, 0.0))
            s_delta = round(new_val - old_val, 6)
            if abs(s_delta) > 1e-6:
                impact = round(years_forward * self.predictor.momentum_factor * weight * s_delta, 4)
                changed_inputs.append(
                    ChangedForecastInput(
                        input_name=key,
                        previous_value=round(old_val, 6),
                        current_value=round(new_val, 6),
                        delta=s_delta,
                        impact_on_projected_cri=impact,
                        explanation=(
                            f"Input '{key}' changed from {old_val:+.4f} to {new_val:+.4f} (delta {s_delta:+.4f}), "
                            f"contributing {impact:+.4f} to the {years_forward}-year projected CRI "
                            f"(weight={weight:+.2f}, momentum={self.predictor.momentum_factor:.2f})."
                        ),
                    )
                )

        newly_added = sorted(set(years_covered) - set(prev_years))
        term_delta = round(curr_terminal - prev_terminal, 4)

        if not changed_inputs:
            summary = (
                f"Prediction remained unchanged at terminal CRI={curr_terminal:.4f} because all underlying "
                f"model inputs (current_cri and feature slopes) are identical to the prior forecast."
            )
        else:
            input_names = ", ".join(f"{c.input_name} ({c.delta:+.4f})" for c in changed_inputs)
            period_clause = f" following ingestion of period(s) {newly_added}" if newly_added else ""
            summary = (
                f"Terminal {years_forward}-year projected CRI changed by {term_delta:+.4f} "
                f"(from {prev_terminal:.4f} to {curr_terminal:.4f}){period_clause} due to changed inputs: {input_names}."
            )

        attribution_parts = [c.explanation for c in changed_inputs]
        return ForecastChangeExplanation(
            institution_id=institution_id,
            has_previous_prediction=True,
            previous_current_cri=round(prev_cri, 4),
            new_current_cri=round(current_cri, 4),
            previous_terminal_cri=round(prev_terminal, 4),
            current_terminal_cri=round(curr_terminal, 4),
            terminal_cri_delta=term_delta,
            changed_inputs=changed_inputs,
            newly_added_periods=newly_added,
            summary=summary,
            deterministic_attribution=" | ".join(attribution_parts) if attribution_parts else "No input deltas.",
        )

    def run_what_if_analysis(
        self,
        institution_id: str,
        current_cri: float,
        feature_slopes: Dict[str, float],
        intervention_effects: Dict[str, float],
        years_forward: int = 3,
        latest_year: Optional[int] = None,
    ) -> WhatIfAnalysisResponse:
        """
        Execute deterministic What-If Analysis (replacing 'Forward Trajectory Simulator').
        Every user control must correspond to a real mathematical model input in TrajectoryPredictor.
        """
        unknown_controls = [k for k in intervention_effects.keys() if k not in SUPPORTED_WHAT_IF_CONTROLS]
        if unknown_controls:
            raise ValueError(
                f"Unsupported or decorative What-If control(s) rejected: {unknown_controls}. "
                f"Supported model controls are: {list(SUPPORTED_WHAT_IF_CONTROLS.keys())}."
            )

        sq_raw = self.predictor.predict_trajectory(
            current_cri=current_cri,
            feature_slopes=feature_slopes,
            years_forward=years_forward,
        )
        iv_raw = self.predictor.simulate_intervention(
            current_cri=current_cri,
            feature_slopes=feature_slopes,
            intervention_effects=intervention_effects,
            years_forward=years_forward,
        )

        sq_points = [
            ForecastTrajectoryPoint(
                year_offset=pt.year_offset,
                target_period=(latest_year + pt.year_offset) if latest_year else None,
                projected_cri=round(pt.projected_cri, 4),
                confidence_band_low=round(pt.confidence_band_low, 4),
                confidence_band_high=round(pt.confidence_band_high, 4),
                scenario=pt.scenario,
            )
            for pt in sq_raw
        ]
        iv_points = [
            ForecastTrajectoryPoint(
                year_offset=pt.year_offset,
                target_period=(latest_year + pt.year_offset) if latest_year else None,
                projected_cri=round(pt.projected_cri, 4),
                confidence_band_low=round(pt.confidence_band_low, 4),
                confidence_band_high=round(pt.confidence_band_high, 4),
                scenario=pt.scenario,
            )
            for pt in iv_raw
        ]

        modified_slopes = dict(feature_slopes)
        mappings: List[WhatIfControlMapping] = []
        reason_parts: List[str] = []

        for ctrl_key, ctrl_val in intervention_effects.items():
            target_slope, sign_mult, desc = SUPPORTED_WHAT_IF_CONTROLS[ctrl_key]
            orig_val = float(modified_slopes.get(target_slope, 0.0))
            new_val = round(orig_val + sign_mult * float(ctrl_val), 6)
            modified_slopes[target_slope] = new_val
            weight = self.predictor._SLOPE_WEIGHTS.get(target_slope, 0.0)
            annual_cri_effect = round(self.predictor.momentum_factor * weight * (new_val - orig_val), 5)

            mappings.append(
                WhatIfControlMapping(
                    control_key=ctrl_key,
                    control_value=float(ctrl_val),
                    target_model_input=target_slope,
                    original_input_value=round(orig_val, 6),
                    modified_input_value=new_val,
                    mathematical_effect=(
                        f"{desc} Shifts '{target_slope}' from {orig_val:+.4f} to {new_val:+.4f}, "
                        f"changing annual CRI momentum by {annual_cri_effect:+.5f}/year."
                    ),
                )
            )
            reason_parts.append(
                f"{ctrl_key}={ctrl_val:+.3f} adjusted model input '{target_slope}' from {orig_val:+.4f} to {new_val:+.4f} "
                f"(annual CRI impact {annual_cri_effect:+.4f}/yr)"
            )

        y1_delta = round(iv_points[0].projected_cri - sq_points[0].projected_cri, 4) if sq_points and iv_points else 0.0
        term_delta = round(iv_points[-1].projected_cri - sq_points[-1].projected_cri, 4) if sq_points and iv_points else 0.0
        risk_reduction = round(sq_points[-1].projected_cri - iv_points[-1].projected_cri, 4) if sq_points and iv_points else 0.0

        if risk_reduction > 0.0001:
            direction = "RISK_REDUCED"
        elif risk_reduction < -0.0001:
            direction = "RISK_INCREASED"
        else:
            direction = "NO_CHANGE"

        if reason_parts:
            reason_for_change = (
                f"Applying intervention controls ({'; '.join(reason_parts)}) modified the composite trajectory "
                f"slope from {self.predictor._compute_slope_composite(feature_slopes):+.4f} to "
                f"{self.predictor._compute_slope_composite(modified_slopes):+.4f}, shifting Year +{years_forward} "
                f"projected CRI from {sq_points[-1].projected_cri:.4f} (baseline) to {iv_points[-1].projected_cri:.4f} "
                f"(net risk reduction: {risk_reduction:+.4f})."
            )
        else:
            reason_for_change = "No intervention controls were modified; projected trajectory equals baseline."

        baseline_summary = WhatIfBaselineSummary(
            current_cri=round(current_cri, 4),
            feature_slopes=feature_slopes,
            baseline_trajectory=sq_points,
            terminal_projected_cri=sq_points[-1].projected_cri if sq_points else round(current_cri, 4),
            method_used="Deterministic First-Order Autoregressive Momentum Model (STATUS_QUO)",
        )
        intervention_spec = WhatIfInterventionSpec(
            applied_controls=intervention_effects,
            modified_slopes=modified_slopes,
            control_mappings=mappings,
        )
        risk_delta = WhatIfRiskDelta(
            year_1_cri_delta=y1_delta,
            terminal_cri_delta=term_delta,
            risk_reduction_achieved=risk_reduction,
            direction=direction,
        )

        return WhatIfAnalysisResponse(
            institution_id=institution_id,
            analysis_type="What-If Analysis",
            status=ForecastStatus.SUFFICIENT_EVIDENCE,
            baseline=baseline_summary,
            intervention=intervention_spec,
            projected_trajectory=iv_points,
            estimated_risk_change=risk_delta,
            reason_for_change=reason_for_change,
            current_cri=round(current_cri, 4),
            status_quo_trajectory=sq_points,
            intervention_trajectory=iv_points,
            risk_reduction_achieved=risk_reduction,
        )

    def compare_prediction_with_outcome(
        self,
        institution_id: str,
        target_academic_year: int,
        metric_name: str,
        predicted_value: float,
        later_observed_value: float,
        confidence_band_low: Optional[float] = None,
        confidence_band_high: Optional[float] = None,
        prediction_memory_id: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Tuple[PredictionOutcomeComparison, InstitutionalMemoryEntry]:
        """
        Execute the CIP Phase 4 Longitudinal Learning Loop:
        Prediction -> later observation -> outcome comparison -> prediction accuracy -> institutional learning.
        """
        band_lo = confidence_band_low if confidence_band_low is not None else max(0.0, predicted_value - 0.08)
        band_hi = confidence_band_high if confidence_band_high is not None else min(1.0, predicted_value + 0.08)

        signed_error = round(later_observed_value - predicted_value, 4)
        abs_error = round(abs(signed_error), 4)
        within_band = (band_lo - 1e-6) <= later_observed_value <= (band_hi + 1e-6)

        # Normalize accuracy to [0.0, 1.0]
        if metric_name == "composite_risk_index" or (0.0 <= predicted_value <= 1.0 and 0.0 <= later_observed_value <= 1.0):
            accuracy = round(max(0.0, 1.0 - abs_error), 4)
        else:
            denom = max(abs(later_observed_value), abs(predicted_value), 1.0)
            accuracy = round(max(0.0, 1.0 - (abs_error / denom)), 4)

        if within_band:
            outcome_summary = (
                f"Prediction for {metric_name} in {target_academic_year} ({predicted_value:.4f}) matched the "
                f"later observed outcome ({later_observed_value:.4f}) within the confidence band "
                f"[{band_lo:.4f}, {band_hi:.4f}] (accuracy: {accuracy * 100:.1f}%, error: {signed_error:+.4f})."
            )
            learning = (
                f"Longitudinal validation confirmed autoregressive slope calibration for {target_academic_year} "
                f"(accuracy {accuracy * 100:.1f}%). Existing feature slope weights remain well-calibrated for this institution."
            )
        elif signed_error < 0:
            outcome_summary = (
                f"Observed {metric_name} in {target_academic_year} ({later_observed_value:.4f}) was lower (better) "
                f"than predicted ({predicted_value:.4f}) by {signed_error:+.4f} (outside band [{band_lo:.4f}, {band_hi:.4f}], "
                f"accuracy: {accuracy * 100:.1f}%)."
            )
            learning = (
                f"Institutional risk stabilized faster than the prior status-quo slope predicted (error {signed_error:+.4f}). "
                f"Likely drivers: corrective intervention between periods or mean-reversion of a one-off anomaly."
            )
        else:
            outcome_summary = (
                f"Observed {metric_name} in {target_academic_year} ({later_observed_value:.4f}) exceeded "
                f"the predicted value ({predicted_value:.4f}) by {signed_error:+.4f} (outside band [{band_lo:.4f}, {band_hi:.4f}], "
                f"accuracy: {accuracy * 100:.1f}%)."
            )
            learning = (
                f"Institutional deterioration accelerated faster than linear historical momentum projected (error {signed_error:+.4f}). "
                f"Cross-domain compounding or unmodeled external shocks amplified risk in {target_academic_year}."
            )

        if notes:
            learning += f" Context note: {notes}"

        comp_id = f"out_{institution_id}_{target_academic_year}_{metric_name}"
        comparison = PredictionOutcomeComparison(
            comparison_id=comp_id,
            institution_id=institution_id,
            prediction_memory_id=prediction_memory_id,
            target_academic_year=target_academic_year,
            metric_name=metric_name,
            predicted_value=round(predicted_value, 4),
            confidence_band_low=round(band_lo, 4),
            confidence_band_high=round(band_hi, 4),
            later_observed_value=round(later_observed_value, 4),
            signed_error=signed_error,
            absolute_error=abs_error,
            within_confidence_band=within_band,
            prediction_accuracy=accuracy,
            outcome_summary=outcome_summary,
            institutional_learning=learning,
        )

        memory_entry = InstitutionalMemoryEntry(
            memory_id=comp_id,
            institution_id=institution_id,
            category=MemoryCategory.OUTCOME,
            domain="institutional_forecasting",
            metric_or_topic=metric_name,
            academic_year=target_academic_year,
            statement=f"{outcome_summary} Learning: {learning}",
            confidence=accuracy,
            evidence_refs=[prediction_memory_id] if prediction_memory_id else [f"observed:{target_academic_year}"],
            payload=comparison.model_dump(mode="json"),
        )
        return comparison, memory_entry

    def extract_memory_entries_from_intelligence(
        self,
        intel_report: InstitutionalIntelligenceReport,
        forecast: ExplainableForecast,
        cet_history: List[CETRankingSignal],
        admissions_history: List[AdmissionsSignal],
        placements_history: List[PlacementsSignal],
        dynamic_signals: Optional[List[DiscoveredSignal]] = None,
    ) -> List[InstitutionalMemoryEntry]:
        """
        Convert Phase 3 epistemic intelligence statements, Phase 4 explainable forecasts,
        and historical backtest outcomes into distinct persisted InstitutionalMemoryEntry items:
        observed_fact, analysis, inference, prediction, outcome, unknown.
        """
        dynamic_signals = dynamic_signals or []
        inst_id = intel_report.institution_id
        entries: List[InstitutionalMemoryEntry] = []

        def _parse_period_year(tp: Optional[str]) -> Optional[int]:
            if not tp:
                return None
            digits = "".join(ch for ch in str(tp) if ch.isdigit())
            if len(digits) >= 4:
                return int(digits[:4])
            return None

        # 1. observed_fact
        for st in intel_report.epistemic_model.observed_facts:
            entries.append(
                InstitutionalMemoryEntry(
                    memory_id=f"mem_fact_{inst_id}_{st.statement_id}",
                    institution_id=inst_id,
                    category=MemoryCategory.OBSERVED_FACT,
                    domain=st.domain,
                    metric_or_topic=st.metric_name or "observed_metric",
                    academic_year=_parse_period_year(st.time_period),
                    statement=st.statement,
                    confidence=st.confidence,
                    evidence_refs=st.evidence_refs,
                    payload=st.model_dump(mode="json"),
                )
            )

        # 2. analysis
        for st in intel_report.epistemic_model.analyses:
            entries.append(
                InstitutionalMemoryEntry(
                    memory_id=f"mem_ana_{inst_id}_{st.statement_id}",
                    institution_id=inst_id,
                    category=MemoryCategory.ANALYSIS,
                    domain=st.domain,
                    metric_or_topic=st.metric_name or "baseline_or_anomaly_analysis",
                    academic_year=_parse_period_year(st.time_period),
                    statement=st.statement,
                    confidence=st.confidence,
                    evidence_refs=st.evidence_refs,
                    payload=st.model_dump(mode="json"),
                )
            )

        # 3. inference (hypotheses & cross-signal findings)
        for st in intel_report.epistemic_model.inferences:
            entries.append(
                InstitutionalMemoryEntry(
                    memory_id=f"mem_inf_{inst_id}_{st.statement_id}",
                    institution_id=inst_id,
                    category=MemoryCategory.INFERENCE,
                    domain=st.domain,
                    metric_or_topic=st.metric_name or "cross_signal_hypothesis",
                    academic_year=_parse_period_year(st.time_period),
                    statement=st.statement,
                    confidence=st.confidence,
                    evidence_refs=st.evidence_refs,
                    payload=st.model_dump(mode="json"),
                )
            )

        # 4. prediction (from Phase 4 ExplainableForecast)
        if forecast.status == ForecastStatus.SUFFICIENT_EVIDENCE and forecast.prediction:
            terminal = forecast.prediction[-1]
            pred_id = f"mem_pred_{inst_id}_{terminal.target_period or terminal.year_offset}"
            entries.append(
                InstitutionalMemoryEntry(
                    memory_id=pred_id,
                    institution_id=inst_id,
                    category=MemoryCategory.PREDICTION,
                    domain="institutional_forecasting",
                    metric_or_topic="composite_risk_index",
                    academic_year=terminal.target_period,
                    statement=(
                        f"Explainable Forecast ({forecast.horizon}) using {forecast.method_actually_used}: "
                        f"CRI projected from {forecast.current_cri:.4f} to {terminal.projected_cri:.4f} "
                        f"(band [{terminal.confidence_band_low:.4f}, {terminal.confidence_band_high:.4f}], "
                        f"confidence {forecast.confidence:.2f})."
                    ),
                    confidence=forecast.confidence,
                    evidence_refs=forecast.historical_evidence[:3],
                    payload=forecast.model_dump(mode="json"),
                )
            )

        # 5. unknown (from Phase 3 unknowns + investigation missing information + insufficient forecast state)
        for st in intel_report.epistemic_model.unknown_and_insufficient_evidence:
            entries.append(
                InstitutionalMemoryEntry(
                    memory_id=f"mem_unk_{inst_id}_{st.statement_id}",
                    institution_id=inst_id,
                    category=MemoryCategory.UNKNOWN,
                    domain=st.domain,
                    metric_or_topic=st.metric_name or "unresolved_question",
                    academic_year=_parse_period_year(st.time_period),
                    statement=st.statement,
                    confidence=None,
                    evidence_refs=st.evidence_refs,
                    payload=st.model_dump(mode="json"),
                )
            )

        for inv in intel_report.investigations:
            for idx, m_info in enumerate(inv.missing_information):
                entries.append(
                    InstitutionalMemoryEntry(
                        memory_id=f"mem_unk_{inst_id}_{inv.investigation_id}_missing_{idx}",
                        institution_id=inst_id,
                        category=MemoryCategory.UNKNOWN,
                        domain=inv.domain,
                        metric_or_topic="missing_corroborating_evidence",
                        academic_year=None,
                        statement=m_info,
                        confidence=None,
                        evidence_refs=[inv.investigation_id],
                        payload={"investigation_id": inv.investigation_id, "missing_information": m_info},
                    )
                )


        if forecast.status == ForecastStatus.INSUFFICIENT_EVIDENCE and forecast.insufficient_evidence_reason:
            entries.append(
                InstitutionalMemoryEntry(
                    memory_id=f"mem_unk_{inst_id}_forecast_insufficient_history",
                    institution_id=inst_id,
                    category=MemoryCategory.UNKNOWN,
                    domain="institutional_forecasting",
                    metric_or_topic="composite_risk_index_forecast",
                    academic_year=None,
                    statement=forecast.insufficient_evidence_reason,
                    confidence=0.0,
                    evidence_refs=[],
                    payload={"status": forecast.status.value, "coverage": forecast.data_coverage.model_dump(mode="json")},
                )
            )

        # 6. Automatic longitudinal hindcast outcome comparison if >= 3 periods exist
        years_covered = forecast.data_coverage.years_covered
        if len(years_covered) >= 3:
            target_yr = years_covered[-1]
            prior_cet = [c for c in cet_history if c.academic_year < target_yr]
            prior_adm = [a for a in admissions_history if a.academic_year < target_yr]
            prior_plc = [p for p in placements_history if p.academic_year < target_yr]
            prior_dyn = [
                d for d in dynamic_signals
                if d.context.academic_year is not None and d.context.academic_year < target_yr
            ]
            prior_slopes, prior_cov, _ = self.compute_slopes_and_coverage(
                prior_cet, prior_adm, prior_plc, prior_dyn
            )
            if prior_cov.is_sufficient:
                prior_assess = self.crisis_engine.evaluate_institution(
                    institution_id=inst_id,
                    cet_history=prior_cet,
                    admissions_history=prior_adm,
                    placements_history=prior_plc,
                    dynamic_signals=prior_dyn,
                )
                prior_pts = self.predictor.predict_trajectory(
                    current_cri=prior_assess.composite_risk_index,
                    feature_slopes=prior_slopes,
                    years_forward=1,
                )
                if prior_pts and forecast.current_cri is not None:
                    pt1 = prior_pts[0]
                    _, outcome_entry = self.compare_prediction_with_outcome(
                        institution_id=inst_id,
                        target_academic_year=target_yr,
                        metric_name="composite_risk_index",
                        predicted_value=pt1.projected_cri,
                        later_observed_value=forecast.current_cri,
                        confidence_band_low=pt1.confidence_band_low,
                        confidence_band_high=pt1.confidence_band_high,
                        prediction_memory_id=f"hindcast_{inst_id}_{ years_covered[-2] }_to_{target_yr}",
                        notes=f"Automated longitudinal backtest comparing {years_covered[-2]} 1-year forecast against {target_yr} observed CRI.",
                    )
                    entries.append(outcome_entry)

        return entries

    def build_memory_store_view(
        self,
        institution_id: str,
        persisted_entries: List[InstitutionalMemoryEntry],
        intel_report: Optional[InstitutionalIntelligenceReport] = None,
        previous_assessments: Optional[List[CrisisAssessment]] = None,
    ) -> InstitutionalMemoryStoreView:
        """
        Assemble the complete CIP Phase 4 InstitutionalMemoryStoreView with:
        - entries_by_category (observed_fact, analysis, inference, prediction, outcome, user_feedback, unknown)
        - historical_signals
        - baselines
        - previous_analyses
        - predictions
        - interventions
        - outcomes
        - validated_rejected_hypotheses
        - unresolved_questions
        - user_feedback_log
        """
        previous_assessments = previous_assessments or []
        entries_by_cat: Dict[str, List[InstitutionalMemoryEntry]] = {
            cat.value: [] for cat in MemoryCategory
        }
        for e in persisted_entries:
            cat_key = e.category.value if isinstance(e.category, MemoryCategory) else str(e.category)
            entries_by_cat.setdefault(cat_key, []).append(e)

        # Feedback lookup by target_id so user feedback can update hypothesis status without becoming observed_fact
        feedback_records: List[UserFeedbackRecord] = []
        feedback_by_target: Dict[str, FeedbackVerdict] = {}
        for fb_entry in entries_by_cat.get(MemoryCategory.USER_FEEDBACK.value, []):
            try:
                rec = UserFeedbackRecord.model_validate(fb_entry.payload)
                feedback_records.append(rec)
                feedback_by_target[rec.target_id] = rec.verdict
            except Exception:
                pass

        # 1. historical_signals
        historical_signals = [
            {
                "memory_id": e.memory_id,
                "domain": e.domain,
                "metric_or_topic": e.metric_or_topic,
                "academic_year": e.academic_year,
                "statement": e.statement,
                "evidence_refs": e.evidence_refs,
            }
            for e in entries_by_cat.get(MemoryCategory.OBSERVED_FACT.value, [])
        ]

        # 2. baselines
        baselines: List[Dict[str, Any]] = []
        if intel_report:
            baselines = [b.model_dump(mode="json") for b in intel_report.baselines]

        # 3. previous_analyses
        previous_analyses: List[Dict[str, Any]] = []
        for a in previous_assessments:
            previous_analyses.append(
                {
                    "assessment_timestamp": a.assessment_timestamp.isoformat(),
                    "composite_risk_index": a.composite_risk_index,
                    "risk_level": a.risk_level,
                    "primary_driving_signal": a.primary_driving_signal,
                    "anomalies_count": len(a.anomalies_detected),
                }
            )
        for ana_entry in entries_by_cat.get(MemoryCategory.ANALYSIS.value, []):
            if ana_entry.payload.get("analysis_type") == "What-If Analysis":
                continue
            previous_analyses.append(
                {
                    "memory_id": ana_entry.memory_id,
                    "domain": ana_entry.domain,
                    "metric_or_topic": ana_entry.metric_or_topic,
                    "academic_year": ana_entry.academic_year,
                    "statement": ana_entry.statement,
                }
            )

        # 4. predictions
        predictions = [
            {
                "memory_id": e.memory_id,
                "academic_year": e.academic_year,
                "statement": e.statement,
                "confidence": e.confidence,
                "payload": e.payload,
            }
            for e in entries_by_cat.get(MemoryCategory.PREDICTION.value, [])
        ]

        # 5. interventions (stored in analysis category with analysis_type == 'What-If Analysis')
        interventions = [
            e.payload
            for e in entries_by_cat.get(MemoryCategory.ANALYSIS.value, [])
            if e.payload.get("analysis_type") == "What-If Analysis"
        ]

        # 6. outcomes
        outcomes: List[PredictionOutcomeComparison] = []
        for out_entry in entries_by_cat.get(MemoryCategory.OUTCOME.value, []):
            try:
                outcomes.append(PredictionOutcomeComparison.model_validate(out_entry.payload))
            except Exception:
                pass

        # 7. validated_rejected_hypotheses
        hypotheses: List[HypothesisRecord] = []
        for inf_entry in entries_by_cat.get(MemoryCategory.INFERENCE.value, []):
            verdict = (
                feedback_by_target.get(inf_entry.memory_id)
                or feedback_by_target.get(inf_entry.payload.get("statement_id", ""))
            )
            if verdict == FeedbackVerdict.CONFIRMED:
                h_status = "VALIDATED"
            elif verdict == FeedbackVerdict.INCORRECT:
                h_status = "REJECTED"
            elif verdict == FeedbackVerdict.INSUFFICIENT_EVIDENCE:
                h_status = "INSUFFICIENT_EVIDENCE"
            else:
                h_status = "UNDER_REVIEW"

            hypotheses.append(
                HypothesisRecord(
                    hypothesis_id=inf_entry.memory_id,
                    domain=inf_entry.domain,
                    metric_or_topic=inf_entry.metric_or_topic,
                    statement=inf_entry.statement,
                    status=h_status,
                    supporting_evidence=inf_entry.evidence_refs,
                    feedback_verdict=verdict,
                )
            )

        # Also include any feedback targeting a hypothesis ID not yet in inferences
        existing_h_ids = {h.hypothesis_id for h in hypotheses}
        for rec in feedback_records:
            if rec.target_id not in existing_h_ids:
                if rec.verdict == FeedbackVerdict.CONFIRMED:
                    h_status = "VALIDATED"
                elif rec.verdict == FeedbackVerdict.INCORRECT:
                    h_status = "REJECTED"
                else:
                    h_status = "INSUFFICIENT_EVIDENCE"
                hypotheses.append(
                    HypothesisRecord(
                        hypothesis_id=rec.target_id,
                        domain=rec.domain,
                        metric_or_topic=rec.metric_or_topic,
                        statement=rec.reviewer_notes or f"Reviewed finding {rec.target_id}",
                        status=h_status,
                        supporting_evidence=[rec.target_id],
                        feedback_verdict=rec.verdict,
                    )
                )

        # 8. unresolved_questions
        unresolved_questions = [
            e.statement for e in entries_by_cat.get(MemoryCategory.UNKNOWN.value, [])
        ]
        if intel_report:
            for inv in intel_report.investigations:
                for q in inv.missing_information:
                    if q not in unresolved_questions:
                        unresolved_questions.append(q)


        return InstitutionalMemoryStoreView(
            institution_id=institution_id,
            entries_by_category=entries_by_cat,
            historical_signals=historical_signals,
            baselines=baselines,
            previous_analyses=previous_analyses,
            predictions=predictions,
            interventions=interventions,
            outcomes=outcomes,
            validated_rejected_hypotheses=hypotheses,
            unresolved_questions=unresolved_questions,
            user_feedback_log=feedback_records,
        )
