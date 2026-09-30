"""
CIP Phase 3: Institutional Intelligence Engine.

Implements:
1. Explicit Epistemic Separation:
   OBSERVED_FACT -> ANALYSIS -> INFERENCE -> PREDICTION -> RECOMMENDATION
   plus UNKNOWN_INSUFFICIENT_EVIDENCE.
2. Institution-Specific Historical Baselines (never fabricated from insufficient history).
3. Multi-Dimensional Anomaly Evaluation (magnitude, historical deviation, persistence,
   coverage, materiality, cross-signal confirmation, and 6-part explanation).
4. 5-Stage Risk Progression:
   Observation -> Anomaly -> Emerging Risk -> Institutional Risk -> Crisis
   (never labeling every anomaly a crisis).
5. Cross-Signal Intelligence (inspecting related signals across connected institutional
   domains and distinguishing correlation from causation via 'potential contributing factor').
6. Structured Investigation Objects (finding, evidence, related_signals,
   potential_contributing_factors, alternative_explanations, confidence, missing_information).
7. Early Warning Detection:
   weak_signal -> repeated_anomaly -> cross_signal_confirmation -> emerging_risk.
8. What-Changed Analysis:
   new_signals, worsening_signals, improving_signals, resolved_risks, new_risks,
   changed_relationships, changed_forecasts.
"""

import math
import statistics
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from src.contracts import (
    AdmissionsSignal,
    CETRankingSignal,
    CrisisAssessment,
    DataQualityIssue,
    DiscoveredSignal,
    EarlyWarningIndicator,
    EarlyWarningStage,
    EpistemicIntelligenceModel,
    EpistemicStatement,
    EvaluatedAnomaly,
    ForecastChangeDelta,
    HistoricalBaseline,
    InstitutionalContextAssociation,
    InstitutionalIntelligenceReport,
    InvestigationObject,
    PlacementsSignal,
    RelatedSignalInspection,
    RelationshipChangeDelta,
    RiskChangeDelta,
    RiskProgressionItem,
    RiskProgressionLadder,
    RiskStage,
    SignalChangeDelta,
    SignalProvenance,
    WhatChangedReport,
    CrossSignalFinding,
)
from src.engine.crisis_scorer import CrisisIntelligenceEngine
from src.engine.features import _linear_slope, _pct_change, extract_institutional_features
from src.engine.predictor import TrajectoryPredictor


# ---------------------------------------------------------------------------
# Domain Materiality Weights & Cross-Signal Knowledge Graph
# ---------------------------------------------------------------------------

DOMAIN_MATERIALITY: Dict[str, float] = {
    "admissions": 0.90,
    "placements": 0.90,
    "academics": 0.88,
    "faculty": 0.88,
    "finance": 0.88,
    "retention": 0.86,
    "accreditation": 0.85,
    "compliance": 0.85,
    "research": 0.78,
    "infrastructure": 0.76,
    "grievances": 0.75,
    "attendance": 0.74,
    "ranking": 0.74,
    "internships": 0.72,
    "industry": 0.70,
    "feedback": 0.70,
    "students": 0.72,
    "general_institutional": 0.60,
}

# Maps a primary domain to related domains that should be inspected when a significant signal changes
CROSS_SIGNAL_GRAPH: Dict[str, List[str]] = {
    "placements": ["internships", "industry", "academics", "feedback", "faculty", "admissions"],
    "admissions": ["ranking", "placements", "feedback", "accreditation", "infrastructure", "faculty"],
    "academics": ["attendance", "faculty", "grievances", "infrastructure", "retention", "feedback"],
    "retention": ["attendance", "academics", "grievances", "finance", "faculty", "feedback"],
    "faculty": ["finance", "research", "grievances", "academics", "retention"],
    "research": ["faculty", "finance", "industry", "infrastructure", "accreditation"],
    "finance": ["admissions", "faculty", "infrastructure", "compliance", "research"],
    "accreditation": ["faculty", "research", "academics", "infrastructure", "placements", "compliance"],
    "compliance": ["accreditation", "finance", "faculty", "infrastructure", "grievances"],
    "grievances": ["faculty", "infrastructure", "academics", "retention", "feedback", "attendance"],
    "attendance": ["academics", "retention", "grievances", "faculty", "feedback"],
    "internships": ["industry", "placements", "academics", "feedback"],
    "industry": ["internships", "placements", "research", "academics"],
    "feedback": ["academics", "faculty", "infrastructure", "grievances", "placements"],
    "infrastructure": ["finance", "academics", "research", "feedback", "grievances"],
    "ranking": ["admissions", "placements", "research", "accreditation", "faculty"],
    "students": ["admissions", "retention", "attendance", "grievances", "feedback"],
    "general_institutional": ["academics", "finance", "faculty", "admissions", "placements"],
}

# Domain-specific plausible alternative explanations (non-causal hygiene)
DOMAIN_ALTERNATIVE_EXPLANATIONS: Dict[str, List[str]] = {
    "placements": [
        "Macroeconomic or IT/core sector-wide hiring slowdown external to the institution.",
        "Changes in graduating cohort eligibility criteria or higher proportion of students opting for higher studies.",
        "Reporting cutoff timing difference (e.g., interim placement window vs final graduating batch count).",
    ],
    "admissions": [
        "State-level CET/counseling schedule shifts or seat matrix expansion across peer institutions.",
        "Planned intake restructuring or introduction of new specialized branches cannibalizing legacy seat demand.",
    ],
    "faculty": [
        "Scheduled superannuation wave or completion of fixed-term visiting/adjunct contracts.",
        "Temporary transition during departmental reorganization or phased recruitment cycle.",
    ],
    "academics": [
        "Transition to stricter university-wide evaluation standards or revised curriculum grading rubrics.",
        "Single-cohort disruption or examination calendar compression.",
    ],
    "retention": [
        "First-year inter-college branch transfer window or lateral migration during counseling rounds.",
        "Administrative reclassification of inactive enrollments vs active dropouts.",
    ],
    "finance": [
        "Timing mismatch between state scholarship/fee reimbursement disbursement and fiscal year-end cutoff.",
        "Planned capital expenditure cycle temporarily lowering liquid operating reserves.",
    ],
    "research": [
        "Multi-year grant or patent publication lag where outputs cluster in subsequent academic years.",
        "Shift in indexing criteria (e.g., Scopus/WoS only vs all conference proceedings).",
    ],
}


def _is_unfavorable_delta(delta: float, polarity: str) -> bool:
    """Return True if a numerical delta moves in the risk-increasing direction."""
    if abs(delta) < 1e-9:
        return False
    if polarity == "higher_is_better":
        return delta < 0
    if polarity == "lower_is_better":
        return delta > 0
    return False


def _unfavorable_magnitude_pct(old_val: float, new_val: float, polarity: str, unit: Optional[str]) -> float:
    """
    Compute a positive percentage representing the magnitude of unfavorable change.
    Returns 0.0 if the change is favorable or neutral.
    """
    delta = new_val - old_val
    if not _is_unfavorable_delta(delta, polarity):
        return 0.0
    if unit == "ratio" and abs(old_val) <= 1.0 and abs(new_val) <= 1.0:
        # Express ratio changes in percentage points so 0.05 -> 0.35 vacancy is +30%
        return abs(delta) * 100.0
    if abs(old_val) < 1e-6:
        return abs(delta) * 100.0 if abs(delta) <= 1.0 else abs(delta)
    return (abs(delta) / abs(old_val)) * 100.0


class InstitutionalIntelligenceEngine:
    """
    CIP Phase 3 Institutional Intelligence Engine.
    Operates deterministically over canonical and dynamically discovered institutional signals.
    """

    def __init__(self):
        self.crisis_engine = CrisisIntelligenceEngine()
        self.predictor = TrajectoryPredictor()

    # ------------------------------------------------------------------
    # Public Entrypoint
    # ------------------------------------------------------------------
    def analyze_institution(
        self,
        institution_id: str,
        cet_history: Optional[List[CETRankingSignal]] = None,
        admissions_history: Optional[List[AdmissionsSignal]] = None,
        placements_history: Optional[List[PlacementsSignal]] = None,
        dynamic_signals: Optional[List[DiscoveredSignal]] = None,
        previous_assessments: Optional[List[CrisisAssessment]] = None,
        data_quality_issues: Optional[List[DataQualityIssue]] = None,
    ) -> InstitutionalIntelligenceReport:
        cet_list = cet_history or []
        adm_list = admissions_history or []
        plc_list = placements_history or []
        dyn_list = dynamic_signals or []
        prev_assessments = previous_assessments or []
        dq_issues = data_quality_issues or []

        # 1. Deterministic Crisis Assessment (preserves validated CRI math)
        current_assessment = self.crisis_engine.evaluate_institution(
            institution_id=institution_id,
            cet_history=cet_list,
            admissions_history=adm_list,
            placements_history=plc_list,
            dynamic_signals=dyn_list,
        )

        # 2. Unify canonical + dynamic signals into a complete signal pool
        unified_signals = self._unify_signals(
            institution_id=institution_id,
            cet_history=cet_list,
            admissions_history=adm_list,
            placements_history=plc_list,
            dynamic_signals=dyn_list,
        )

        # 3. Surface active source contradictions
        contradictions = self._surface_contradictions(unified_signals)

        # 4. Build Institution-Specific Historical Baselines (never fabricating when history is insufficient)
        baselines = self.build_baselines(institution_id=institution_id, signals=unified_signals)

        # 5. Evaluate Multi-Dimensional Anomalies (magnitude, deviation, persistence, coverage, materiality, cross-signal)
        evaluated_anomalies = self.evaluate_anomalies(
            institution_id=institution_id,
            signals=unified_signals,
            baselines=baselines,
            admissions_history=adm_list,
        )

        # 6. Build 5-Stage Risk Progression Ladder (Observation -> Anomaly -> Emerging Risk -> Institutional Risk -> Crisis)
        risk_progression = self.build_risk_progression(
            institution_id=institution_id,
            assessment=current_assessment,
            signals=unified_signals,
            baselines=baselines,
            evaluated_anomalies=evaluated_anomalies,
        )

        # 7. Cross-Signal Intelligence (inspect related signals & distinguish correlation from causation)
        cross_signal_findings = self.build_cross_signal_findings(
            signals=unified_signals,
            baselines=baselines,
            evaluated_anomalies=evaluated_anomalies,
        )

        # 8. Build Investigation Objects for Major Findings
        investigations = self.build_investigations(
            signals=unified_signals,
            baselines=baselines,
            evaluated_anomalies=evaluated_anomalies,
            cross_signal_findings=cross_signal_findings,
            contradictions=contradictions,
            dq_issues=dq_issues,
        )

        # 9. Early Warning Detection (weak_signal -> repeated_anomaly -> cross_signal_confirmation -> emerging_risk)
        early_warnings = self.build_early_warnings(
            signals=unified_signals,
            baselines=baselines,
            evaluated_anomalies=evaluated_anomalies,
        )

        # 10. What-Changed Analysis (new/worsening/improving signals, resolved/new risks, changed relationships, changed forecasts)
        what_changed = self.build_what_changed(
            institution_id=institution_id,
            cet_history=cet_list,
            admissions_history=adm_list,
            placements_history=plc_list,
            signals=unified_signals,
            baselines=baselines,
            evaluated_anomalies=evaluated_anomalies,
            current_assessment=current_assessment,
            previous_assessments=prev_assessments,
        )

        # 11. Explicit Epistemic Separation Model
        epistemic_model = self.build_epistemic_model(
            institution_id=institution_id,
            signals=unified_signals,
            baselines=baselines,
            evaluated_anomalies=evaluated_anomalies,
            cross_signal_findings=cross_signal_findings,
            investigations=investigations,
            what_changed=what_changed,
            current_assessment=current_assessment,
            cet_history=cet_list,
            admissions_history=adm_list,
            placements_history=plc_list,
            contradictions=contradictions,
            dq_issues=dq_issues,
        )

        return InstitutionalIntelligenceReport(
            institution_id=institution_id,
            epistemic_model=epistemic_model,
            baselines=baselines,
            evaluated_anomalies=evaluated_anomalies,
            risk_progression=risk_progression,
            cross_signal_findings=cross_signal_findings,
            investigations=investigations,
            early_warnings=early_warnings,
            what_changed=what_changed,
            contradictions_surfaced=contradictions,
        )

    # ------------------------------------------------------------------
    # Step 1: Signal Unification & Contradiction Surfacing
    # ------------------------------------------------------------------
    def _unify_signals(
        self,
        institution_id: str,
        cet_history: List[CETRankingSignal],
        admissions_history: List[AdmissionsSignal],
        placements_history: List[PlacementsSignal],
        dynamic_signals: List[DiscoveredSignal],
    ) -> List[DiscoveredSignal]:
        """
        Combine canonical and dynamic signals without duplicating identical observations,
        while preserving all contradictory observations.
        """
        out: List[DiscoveredSignal] = list(dynamic_signals)
        existing_keys: Set[Tuple[str, str, Optional[str], Optional[int], float]] = set()
        for s in out:
            if s.value is not None:
                existing_keys.add(
                    (
                        s.domain,
                        s.metric_name,
                        (s.context.department or "INSTITUTIONAL").upper(),
                        s.context.academic_year,
                        round(float(s.value), 4),
                    )
                )

        def _add_canonical(
            domain: str,
            metric_name: str,
            metric_label: str,
            val: float,
            unit: str,
            polarity: str,
            dept: str,
            yr: int,
            src_id: str,
            excerpt: str,
            risk_contrib: Optional[float] = None,
        ):
            key = (domain, metric_name, (dept or "INSTITUTIONAL").upper(), yr, round(float(val), 4))
            if key in existing_keys:
                return
            existing_keys.add(key)
            out.append(
                DiscoveredSignal(
                    signal_id=f"canon_{domain}_{metric_name}_{dept}_{yr}",
                    domain=domain,
                    metric_name=metric_name,
                    metric_label=metric_label,
                    value=float(val),
                    raw_value=str(val),
                    unit=unit,
                    polarity=polarity,
                    risk_contribution=risk_contrib,
                    context=InstitutionalContextAssociation(
                        institution_id=institution_id,
                        department=dept,
                        time_period=str(yr),
                        academic_year=yr,
                        source=src_id,
                        context_evidence={
                            "department": f"Canonical signal department '{dept}'",
                            "academic_year": f"Canonical signal academic_year '{yr}'",
                        },
                    ),
                    provenance=SignalProvenance(
                        source=src_id,
                        document=src_id,
                        format_type="CANONICAL",
                        page_or_section=f"AY {yr} {domain.title()}",
                        excerpt_or_reference=excerpt,
                        extraction_confidence=0.98,
                    ),
                )
            )

        for adm in admissions_history:
            src = adm.provenance.source_id if adm.provenance else "canonical_admissions"
            enrolled = getattr(adm, "enrolled_count", getattr(adm, "admitted_students", 0))
            _add_canonical(
                "admissions",
                "enrolled_count",
                "Enrolled Students",
                float(enrolled),
                "count",
                "higher_is_better",
                adm.department,
                adm.academic_year,
                src,
                f"{adm.department} ({adm.academic_year}): Enrolled={enrolled}/{adm.sanctioned_intake}, Vacancy={adm.vacancy_rate:.1%}",
                min(1.0, adm.vacancy_rate * 1.5),
            )
            _add_canonical(
                "admissions",
                "vacancy_rate",
                "Seat Vacancy Rate",
                float(adm.vacancy_rate),
                "ratio",
                "lower_is_better",
                adm.department,
                adm.academic_year,
                src,
                f"{adm.department} ({adm.academic_year}): Vacancy rate={adm.vacancy_rate:.1%}",
                min(1.0, adm.vacancy_rate * 1.5),
            )

        for plc in placements_history:
            src = plc.provenance.source_id if plc.provenance else "canonical_placements"
            _add_canonical(
                "placements",
                "placement_percentage",
                "Placement Percentage",
                float(plc.placement_percentage),
                "%",
                "higher_is_better",
                plc.department,
                plc.graduation_year,
                src,
                f"{plc.department} ({plc.graduation_year}): Placed={plc.placed_students}/{plc.eligible_students} ({plc.placement_percentage:.1f}%)",
                max(0.0, min(1.0, (100.0 - plc.placement_percentage) / 100.0)),
            )

        for rnk in cet_history:
            src = rnk.provenance.source_id if rnk.provenance else "canonical_cet"
            _add_canonical(
                "ranking",
                "closing_rank",
                "CET Closing Rank",
                float(rnk.closing_rank),
                "rank",
                "lower_is_better",
                rnk.department,
                rnk.academic_year,
                src,
                f"{rnk.department} ({rnk.academic_year}): Opening={rnk.opening_rank}, Closing={rnk.closing_rank}",
                None,
            )

        return out

    @staticmethod
    def _surface_contradictions(signals: List[DiscoveredSignal]) -> List[Dict[str, Any]]:
        """Group and surface all conflicting signal observations without hiding any source."""
        by_slot: Dict[Tuple[str, str, Optional[str], Optional[str]], List[DiscoveredSignal]] = {}
        for s in signals:
            period_key = str(s.context.academic_year) if s.context.academic_year is not None else s.context.time_period
            slot = (s.domain, s.metric_name, s.context.department, period_key)
            by_slot.setdefault(slot, []).append(s)

        contradictions: List[Dict[str, Any]] = []
        for (domain, metric, dept, period), items in by_slot.items():
            numeric_items = [i for i in items if i.value is not None]
            if len(numeric_items) < 2:
                continue
            vals = [float(i.value) for i in numeric_items]
            spread = max(vals) - min(vals)
            denom = max(abs(min(vals)), 1.0)
            has_flag = any(i.is_contradictory for i in numeric_items)
            if has_flag or (spread > 1e-4 and (spread / denom) > 0.01):
                contradictions.append(
                    {
                        "domain": domain,
                        "metric_name": metric,
                        "department": dept,
                        "time_period": period,
                        "contradiction_group_id": next((i.contradiction_group_id for i in numeric_items if i.contradiction_group_id), f"contra_{domain}_{metric}"),
                        "conflicting_observations": [
                            {
                                "signal_id": i.signal_id,
                                "value": i.value,
                                "raw_value": i.raw_value,
                                "source": i.provenance.document,
                                "location": i.provenance.spreadsheet_location or i.provenance.page_or_section,
                                "excerpt": i.provenance.excerpt_or_reference,
                                "confidence": i.provenance.extraction_confidence,
                            }
                            for i in numeric_items
                        ],
                    }
                )
        return contradictions

    # ------------------------------------------------------------------
    # Step 2: Institution-Specific Historical Baselines
    # ------------------------------------------------------------------
    def build_baselines(
        self,
        institution_id: str,
        signals: List[DiscoveredSignal],
    ) -> List[HistoricalBaseline]:
        """
        Build institution-specific historical baselines per (domain, metric_name, department).
        - Requires >= 3 distinct academic years (>= 2 prior years before latest) to establish
          a statistical baseline (ESTABLISHED).
        - With 2 distinct academic years, reports TWO_PERIOD_COMPARISON_ONLY (populates prior_value
          and latest_value, keeping baseline_mean=None so a multi-year statistical baseline is never fabricated).
        - With <= 1 academic year (or undated), reports INSUFFICIENT_HISTORY with baseline_mean=None.
        """
        grouped: Dict[Tuple[str, str, Optional[str]], List[DiscoveredSignal]] = {}
        for s in signals:
            if s.value is None or s.is_duplicate:
                continue
            key = (s.domain, s.metric_name, s.context.department)
            grouped.setdefault(key, []).append(s)

        baselines: List[HistoricalBaseline] = []
        for (domain, metric_name, dept), obs_list in sorted(
            grouped.items(), key=lambda x: (x[0][0], x[0][1], x[0][2] or "")
        ):
            sample = obs_list[0]
            has_contra = any(o.is_contradictory for o in obs_list)

            # Group by academic_year
            by_year: Dict[int, List[DiscoveredSignal]] = {}
            for o in obs_list:
                if o.context.academic_year is not None:
                    by_year.setdefault(o.context.academic_year, []).append(o)

            # Check if same year has conflicting values
            for yr, yr_items in by_year.items():
                if len(yr_items) > 1:
                    vals = [float(x.value) for x in yr_items if x.value is not None]
                    if max(vals) - min(vals) > 1e-4:
                        has_contra = True

            sorted_years = sorted(by_year.keys())

            if len(sorted_years) <= 1:
                latest_yr = sorted_years[0] if sorted_years else None
                # If multiple conflicting observations exist in that single year, pick highest confidence for display but mark has_contradictions
                latest_val = (
                    float(sorted(by_year[latest_yr], key=lambda x: x.provenance.extraction_confidence, reverse=True)[0].value)
                    if latest_yr is not None
                    else float(obs_list[-1].value)
                )
                status: BaselineStatus = "CONTRADICTORY_HISTORY" if has_contra else "INSUFFICIENT_HISTORY"
                reason = (
                    f"Insufficient historical data for '{sample.metric_label}' "
                    f"({f'{len(sorted_years)} dated period ({latest_yr})' if sorted_years else '0 dated periods'}). "
                    f"CIP requires >= 3 historical periods to establish a statistical baseline and never fabricates baselines."
                )
                if has_contra:
                    reason += " Additionally, conflicting source values exist for this metric."
                baselines.append(
                    HistoricalBaseline(
                        institution_id=institution_id,
                        domain=domain,
                        metric_name=metric_name,
                        metric_label=sample.metric_label,
                        department=dept,
                        program=sample.context.program,
                        unit=sample.unit,
                        polarity=sample.polarity,
                        baseline_status=status,
                        periods_available=sorted_years,
                        historical_periods_used=[],
                        baseline_mean=None,
                        baseline_std=None,
                        baseline_median=None,
                        baseline_min=None,
                        baseline_max=None,
                        trend_slope_per_period=None,
                        latest_period=latest_yr,
                        latest_value=round(latest_val, 4),
                        has_contradictions=has_contra,
                        explanation=reason,
                    )
                )
                continue

            # Resolve representative value per year (highest extraction confidence if multiple sources)
            year_vals: List[Tuple[int, float]] = []
            for yr in sorted_years:
                best_obs = sorted(by_year[yr], key=lambda x: x.provenance.extraction_confidence, reverse=True)[0]
                year_vals.append((yr, float(best_obs.value)))

            latest_yr, latest_val = year_vals[-1]
            prior_yr, prior_val = year_vals[-2]
            all_floats = [v for _, v in year_vals]
            slope = round(_linear_slope(all_floats), 4)

            if len(sorted_years) == 2:
                status = "CONTRADICTORY_HISTORY" if has_contra else "TWO_PERIOD_COMPARISON_ONLY"
                baselines.append(
                    HistoricalBaseline(
                        institution_id=institution_id,
                        domain=domain,
                        metric_name=metric_name,
                        metric_label=sample.metric_label,
                        department=dept,
                        program=sample.context.program,
                        unit=sample.unit,
                        polarity=sample.polarity,
                        baseline_status=status,
                        periods_available=sorted_years,
                        historical_periods_used=[prior_yr],
                        baseline_mean=None,
                        baseline_std=None,
                        baseline_median=None,
                        baseline_min=None,
                        baseline_max=None,
                        trend_slope_per_period=slope,
                        latest_period=latest_yr,
                        latest_value=round(latest_val, 4),
                        prior_period=prior_yr,
                        prior_value=round(prior_val, 4),
                        has_contradictions=has_contra,
                        explanation=(
                            f"Only 2 periods available ({prior_yr}, {latest_yr}) for '{sample.metric_label}'. "
                            f"Period-over-period comparison is supported ({prior_val} -> {latest_val}), "
                            f"but a multi-year statistical distribution baseline is not fabricated from a single prior point."
                        ),
                    )
                )
                continue

            # >= 3 distinct academic years: establish full institution-specific historical baseline from prior periods
            hist_years = [yr for yr, _ in year_vals[:-1]]
            hist_vals = [v for _, v in year_vals[:-1]]
            b_mean = round(statistics.mean(hist_vals), 4)
            b_std = round(statistics.stdev(hist_vals), 4) if len(hist_vals) >= 2 else 0.0
            b_med = round(statistics.median(hist_vals), 4)
            b_min = round(min(hist_vals), 4)
            b_max = round(max(hist_vals), 4)

            baselines.append(
                HistoricalBaseline(
                    institution_id=institution_id,
                    domain=domain,
                    metric_name=metric_name,
                    metric_label=sample.metric_label,
                    department=dept,
                    program=sample.context.program,
                    unit=sample.unit,
                    polarity=sample.polarity,
                    baseline_status="ESTABLISHED",
                    periods_available=sorted_years,
                    historical_periods_used=hist_years,
                    baseline_mean=b_mean,
                    baseline_std=b_std,
                    baseline_median=b_med,
                    baseline_min=b_min,
                    baseline_max=b_max,
                    trend_slope_per_period=slope,
                    latest_period=latest_yr,
                    latest_value=round(latest_val, 4),
                    prior_period=prior_yr,
                    prior_value=round(prior_val, 4),
                    has_contradictions=has_contra,
                    explanation=(
                        f"Institution-specific baseline established for '{sample.metric_label}' "
                        f"({dept or 'Institution-Wide'}) using {len(hist_vals)} historical periods "
                        f"({hist_years[0]}–{hist_years[-1]}): mean={b_mean}, std={b_std}, median={b_med}."
                    ),
                )
            )

        return baselines

    # ------------------------------------------------------------------
    # Step 3: Multi-Dimensional Anomaly Evaluation
    # ------------------------------------------------------------------
    def evaluate_anomalies(
        self,
        institution_id: str,
        signals: List[DiscoveredSignal],
        baselines: List[HistoricalBaseline],
        admissions_history: Optional[List[AdmissionsSignal]] = None,
    ) -> List[EvaluatedAnomaly]:
        """
        Evaluate meaningful change across all metric series using:
        - magnitude
        - historical_deviation
        - persistence
        - coverage
        - materiality
        - cross_signal_confirmation
        And populate the 6 required explanation fields.
        """
        adm_list = admissions_history or []
        intake_by_dept: Dict[str, int] = {}
        for a in sorted(adm_list, key=lambda x: x.academic_year):
            intake_by_dept[a.department] = a.sanctioned_intake
        total_inst_intake = sum(intake_by_dept.values()) or 100

        # Index signals by (domain, metric_name, dept)
        sig_by_key: Dict[Tuple[str, str, Optional[str]], List[DiscoveredSignal]] = {}
        all_depts: Set[str] = set()
        for s in signals:
            if s.value is None or s.is_duplicate:
                continue
            if s.context.department:
                all_depts.add(s.context.department)
            sig_by_key.setdefault((s.domain, s.metric_name, s.context.department), []).append(s)

        total_depts_count = max(len(all_depts), 1)

        # First pass: determine which (domain, metric_name, dept) have unfavorable shift or high risk contribution
        preliminary_unfavorable: Dict[Tuple[str, str, Optional[str]], Dict[str, Any]] = {}

        for b in baselines:
            key = (b.domain, b.metric_name, b.department)
            obs_series = sorted(
                [s for s in sig_by_key.get(key, []) if s.context.academic_year is not None],
                key=lambda x: x.context.academic_year or 0,
            )
            if not obs_series:
                obs_series = sig_by_key.get(key, [])
            if not obs_series:
                continue

            latest_obs = obs_series[-1]
            latest_val = float(latest_obs.value)

            # Determine comparison value and basis
            comp_val: Optional[float] = None
            comp_basis: str = ""
            z_score: Optional[float] = None

            if b.baseline_status == "ESTABLISHED" and b.baseline_mean is not None:
                comp_val = b.baseline_mean
                comp_basis = (
                    f"Institution-specific historical baseline ({b.historical_periods_used[0]}–"
                    f"{b.historical_periods_used[-1]}, mean={b.baseline_mean:.2f}, std={b.baseline_std or 0.0:.2f})"
                )
                if b.baseline_std and b.baseline_std > 1e-6:
                    z_score = round((latest_val - b.baseline_mean) / b.baseline_std, 2)
                elif abs(latest_val - b.baseline_mean) > 1e-4:
                    # Zero variance in prior history followed by a jump
                    z_score = round(3.0 if latest_val > b.baseline_mean else -3.0, 2)
                else:
                    z_score = 0.0
            elif b.prior_value is not None:
                comp_val = b.prior_value
                comp_basis = (
                    f"Prior period comparison (AY {b.prior_period} = {b.prior_value:.2f}; "
                    f"insufficient history for multi-year statistical distribution baseline)"
                )
            else:
                comp_val = None
                comp_basis = (
                    "Single-period observation evaluated against domain risk thresholds "
                    "(insufficient historical periods to establish an institution-specific baseline)"
                )

            # Calculate magnitude & persistence
            unfav_pct = 0.0
            abs_delta: Optional[float] = None
            rel_pct: Optional[float] = None
            if comp_val is not None:
                abs_delta = round(latest_val - comp_val, 4)
                rel_pct = round(_pct_change(comp_val, latest_val), 2)
                unfav_pct = _unfavorable_magnitude_pct(comp_val, latest_val, b.polarity, b.unit)

            # Count consecutive unfavorable periods (persistence)
            # Deduplicate by academic_year
            yr_dedup: List[Tuple[int, float, DiscoveredSignal]] = []
            seen_yrs: Set[int] = set()
            for o in obs_series:
                if o.context.academic_year is not None and o.context.academic_year not in seen_yrs:
                    seen_yrs.add(o.context.academic_year)
                    yr_dedup.append((o.context.academic_year, float(o.value), o))

            persistence_periods = 0
            if len(yr_dedup) >= 2:
                for idx in range(len(yr_dedup) - 1, 0, -1):
                    step_delta = yr_dedup[idx][1] - yr_dedup[idx - 1][1]
                    if _is_unfavorable_delta(step_delta, b.polarity):
                        persistence_periods += 1
                    else:
                        break

            # Risk contribution from domain heuristic
            risk_contrib = latest_obs.risk_contribution or 0.0
            unfav_z = 0.0
            if z_score is not None:
                if b.polarity == "higher_is_better" and z_score < 0:
                    unfav_z = abs(z_score)
                elif b.polarity == "lower_is_better" and z_score > 0:
                    unfav_z = abs(z_score)

            # Decide whether this metric exhibits a meaningful unfavorable change or threshold anomaly
            is_meaningful_anomaly = (
                unfav_pct >= 10.0
                or unfav_z >= 1.8
                or risk_contrib >= 0.45
                or (persistence_periods >= 2 and unfav_pct >= 5.0)
            )
            is_weak_or_notable = (
                unfav_pct >= 4.0
                or unfav_z >= 1.0
                or risk_contrib >= 0.30
                or persistence_periods >= 1
            )

            if is_meaningful_anomaly or is_weak_or_notable:
                preliminary_unfavorable[key] = {
                    "baseline": b,
                    "latest_obs": latest_obs,
                    "obs_series": obs_series,
                    "latest_val": latest_val,
                    "comp_val": comp_val,
                    "comp_basis": comp_basis,
                    "z_score": z_score,
                    "unfav_z": unfav_z,
                    "abs_delta": abs_delta,
                    "rel_pct": rel_pct,
                    "unfav_pct": unfav_pct,
                    "persistence_periods": max(persistence_periods, 1 if is_meaningful_anomaly else 0),
                    "risk_contrib": risk_contrib,
                    "is_meaningful_anomaly": is_meaningful_anomaly,
                }

        # Second pass: compute coverage across departments and cross-signal confirmation across connected domains
        anomalous_depts_by_metric: Dict[Tuple[str, str], Set[str]] = {}
        anomalous_domains: Dict[str, List[str]] = {}
        for (domain, metric_name, dept), info in preliminary_unfavorable.items():
            if info["is_meaningful_anomaly"]:
                if dept:
                    anomalous_depts_by_metric.setdefault((domain, metric_name), set()).add(dept)
                anomalous_domains.setdefault(domain, []).append(
                    f"{domain}.{metric_name}" + (f" ({dept})" if dept else "")
                )

        evaluated_anomalies: List[EvaluatedAnomaly] = []
        for (domain, metric_name, dept), info in preliminary_unfavorable.items():
            if not info["is_meaningful_anomaly"]:
                continue

            b: HistoricalBaseline = info["baseline"]
            latest_obs: DiscoveredSignal = info["latest_obs"]
            unfav_pct: float = info["unfav_pct"]
            unfav_z: float = info["unfav_z"]
            risk_contrib: float = info["risk_contrib"]
            persistence_periods: int = info["persistence_periods"]

            # 1. Magnitude score [0.0, 1.0]
            mag_from_pct = min(1.0, unfav_pct / 35.0)
            mag_score = round(max(mag_from_pct, risk_contrib), 3)

            # 2. Historical deviation score [0.0, 1.0]
            if info["z_score"] is not None:
                dev_score = round(min(1.0, unfav_z / 3.5), 3)
            else:
                dev_score = round(min(0.75, mag_score * 0.8), 3)

            # 3. Persistence score [0.0, 1.0]
            if b.baseline_status == "INSUFFICIENT_HISTORY":
                persistence_periods = 1
                pers_score = 0.20
            elif persistence_periods >= 3:
                pers_score = 1.0
            elif persistence_periods == 2:
                pers_score = 0.68
            else:
                pers_score = 0.28

            # 4. Coverage ratio & scope
            if not dept or dept.upper() == "INSTITUTIONAL":
                cov_ratio = 1.0
                cov_scope = "Institution-Wide"
            else:
                affected_depts = sorted(list(anomalous_depts_by_metric.get((domain, metric_name), {dept})))
                cov_ratio = round(min(1.0, len(affected_depts) / float(total_depts_count)), 3)
                if len(affected_depts) > 1:
                    cov_scope = (
                        f"Multi-Departmental ({len(affected_depts)}/{total_depts_count} departments: "
                        f"{', '.join(affected_depts[:4])})"
                    )
                else:
                    cov_scope = f"Departmental ({dept} — 1/{total_depts_count} departments)"

            # 5. Materiality score [0.0, 1.0]
            base_mat = DOMAIN_MATERIALITY.get(domain, 0.70)
            if dept and dept in intake_by_dept and total_inst_intake > 0:
                dept_share = intake_by_dept[dept] / float(total_inst_intake)
                mat_score = round(min(1.0, base_mat * (0.75 + 0.50 * min(0.5, dept_share))), 3)
            else:
                mat_score = round(base_mat, 3)

            # 6. Cross-signal confirmation
            related_domains = CROSS_SIGNAL_GRAPH.get(domain, [])
            confirming: List[str] = []
            for r_dom in related_domains:
                for (k_dom, k_met, k_dept), r_info in preliminary_unfavorable.items():
                    if k_dom == r_dom and r_info["is_meaningful_anomaly"]:
                        if k_dept is None or dept is None or k_dept == dept:
                            sig_tag = f"{k_dom}.{k_met}" + (f" ({k_dept})" if k_dept else "")
                            if sig_tag not in confirming:
                                confirming.append(sig_tag)
            has_cross_confirm = len(confirming) > 0

            # Composite significance score
            comp_sig = round(
                min(
                    1.0,
                    0.25 * mag_score
                    + 0.20 * dev_score
                    + 0.20 * pers_score
                    + 0.15 * cov_ratio
                    + 0.10 * mat_score
                    + 0.10 * (1.0 if has_cross_confirm else 0.0),
                ),
                3,
            )

            # Determine RiskStage (strictly preventing every anomaly from being labeled a Crisis!)
            risk_stage = self._classify_anomaly_risk_stage(
                baseline_status=b.baseline_status,
                mag_score=mag_score,
                dev_score=dev_score,
                persistence_periods=persistence_periods,
                cov_ratio=cov_ratio,
                mat_score=mat_score,
                has_cross_confirm=has_cross_confirm,
                comp_sig=comp_sig,
            )

            if risk_stage == "Crisis":
                severity: str = "CRITICAL"
            elif risk_stage == "Institutional Risk":
                severity = "CRITICAL" if comp_sig >= 0.78 else "HIGH"
            elif risk_stage == "Emerging Risk":
                severity = "HIGH"
            elif risk_stage == "Anomaly":
                severity = "MEDIUM" if comp_sig < 0.58 else "HIGH"
            else:
                severity = "LOW"

            # Build the 6 required human-verifiable explanation strings
            direction_word = (
                "declined"
                if (b.polarity == "higher_is_better")
                else ("increased" if b.polarity == "lower_is_better" else "shifted")
            )
            scope_label = f" in department {dept}" if dept else " (Institution-Wide)"
            what_changed = f"{b.metric_label} ({domain}.{metric_name}){scope_label} {direction_word}."

            unit_sfx = "%" if b.unit == "%" else (f" {b.unit}" if b.unit and b.unit not in ("count", "ratio") else "")
            if info["comp_val"] is not None:
                z_str = f", Z-score: {info['z_score']:+.2f}" if info["z_score"] is not None else " (no multi-year Z-score)"
                by_how_much = (
                    f"Observed {info['latest_val']:.2f}{unit_sfx} vs comparison {info['comp_val']:.2f}{unit_sfx} "
                    f"(absolute delta: {info['abs_delta']:+.2f}{unit_sfx}, relative change: {info['rel_pct']:+.1f}%{z_str})."
                )
            else:
                by_how_much = (
                    f"Observed {info['latest_val']:.2f}{unit_sfx} (single-period observation; "
                    f"domain risk severity score: {risk_contrib:.2f})."
                )

            when_str = (
                f"AY {b.latest_period}"
                if b.latest_period is not None
                else (latest_obs.context.time_period or "Undated period")
            )
            if persistence_periods >= 2 and len(b.periods_available) >= persistence_periods:
                start_streak = b.periods_available[-persistence_periods]
                when_str += f" (persistent across {persistence_periods} consecutive periods: {start_streak}–{b.latest_period})"

            significance_str = (
                f"Classified as '{risk_stage}' (composite significance={comp_sig:.2f}, severity={severity}): "
                f"magnitude={mag_score:.2f}, historical_deviation={dev_score:.2f}, "
                f"persistence={persistence_periods} period(s), coverage={cov_scope}, "
                f"materiality={mat_score:.2f}, cross_signal_confirmation={has_cross_confirm}"
                + (f" ({', '.join(confirming[:3])})" if confirming else "")
                + "."
            )

            ev_list: List[str] = []
            for o in info["obs_series"][-3:]:
                loc = o.provenance.spreadsheet_location or o.provenance.page_or_section or o.provenance.format_type
                ev_list.append(
                    f"[{o.provenance.document} | {loc}] {o.provenance.excerpt_or_reference} "
                    f"(confidence={o.provenance.extraction_confidence:.2f})"
                )

            evaluated_anomalies.append(
                EvaluatedAnomaly(
                    anomaly_id=f"anom_{domain}_{metric_name}_{dept or 'INST'}_{b.latest_period or 'NA'}",
                    domain=domain,
                    metric_name=metric_name,
                    metric_label=b.metric_label,
                    department=dept,
                    academic_year=b.latest_period,
                    observed_value=round(info["latest_val"], 4),
                    comparison_value=round(info["comp_val"], 4) if info["comp_val"] is not None else None,
                    unit=b.unit,
                    polarity=b.polarity,
                    magnitude_score=mag_score,
                    absolute_delta=info["abs_delta"],
                    relative_pct_change=info["rel_pct"],
                    historical_deviation_zscore=info["z_score"],
                    historical_deviation_score=dev_score,
                    persistence_periods=persistence_periods,
                    persistence_score=pers_score,
                    coverage_ratio=cov_ratio,
                    coverage_scope=cov_scope,
                    materiality_score=mat_score,
                    cross_signal_confirmation=has_cross_confirm,
                    confirming_signals=confirming,
                    composite_significance_score=comp_sig,
                    severity=severity,  # type: ignore[arg-type]
                    risk_stage=risk_stage,
                    what_changed=what_changed,
                    by_how_much=by_how_much,
                    when=when_str,
                    comparison_basis=info["comp_basis"],
                    significance=significance_str,
                    evidence=ev_list,
                )
            )

        evaluated_anomalies.sort(key=lambda a: a.composite_significance_score, reverse=True)
        return evaluated_anomalies

    @staticmethod
    def _classify_anomaly_risk_stage(
        baseline_status: str,
        mag_score: float,
        dev_score: float,
        persistence_periods: int,
        cov_ratio: float,
        mat_score: float,
        has_cross_confirm: bool,
        comp_sig: float,
    ) -> RiskStage:
        """
        5-Stage Risk Progression Classifier:
        Observation -> Anomaly -> Emerging Risk -> Institutional Risk -> Crisis.
        Never labels an isolated single-period or unconfirmed anomaly as a Crisis.
        """
        # Without historical comparison, a single observation is an Observation or Anomaly unless extreme + corroborated
        if baseline_status == "INSUFFICIENT_HISTORY":
            if mag_score < 0.55:
                return "Observation"
            if has_cross_confirm and mag_score >= 0.75 and cov_ratio >= 0.5:
                return "Emerging Risk"
            return "Anomaly"

        # Stage 5: Crisis requires persistent (>=2 periods), cross-signal confirmed, broad coverage, severe magnitude
        if (
            persistence_periods >= 2
            and has_cross_confirm
            and cov_ratio >= 0.50
            and mag_score >= 0.75
            and comp_sig >= 0.78
        ):
            return "Crisis"

        # Stage 4: Institutional Risk requires persistence (>=2 periods) AND (cross-signal confirmation OR broad coverage >= 0.50) with high significance
        if (
            persistence_periods >= 2
            and (has_cross_confirm or cov_ratio >= 0.50)
            and mat_score >= 0.74
            and comp_sig >= 0.62
        ):
            return "Institutional Risk"

        # Stage 3: Emerging Risk requires either >=2 persistent periods OR cross-signal confirmation with material magnitude
        if (
            persistence_periods >= 2
            or (has_cross_confirm and mag_score >= 0.50)
            or (cov_ratio >= 0.50 and mag_score >= 0.60)
        ):
            return "Emerging Risk"

        # Stage 2: Anomaly (single-period or localized meaningful deviation)
        if mag_score >= 0.30 or dev_score >= 0.45:
            return "Anomaly"

        # Stage 1: Observation
        return "Observation"

    # ------------------------------------------------------------------
    # Step 4: 5-Stage Risk Progression Ladder
    # ------------------------------------------------------------------
    def build_risk_progression(
        self,
        institution_id: str,
        assessment: CrisisAssessment,
        signals: List[DiscoveredSignal],
        baselines: List[HistoricalBaseline],
        evaluated_anomalies: List[EvaluatedAnomaly],
    ) -> RiskProgressionLadder:
        observations: List[RiskProgressionItem] = []
        anomalies: List[RiskProgressionItem] = []
        emerging_risks: List[RiskProgressionItem] = []
        institutional_risks: List[RiskProgressionItem] = []
        crises: List[RiskProgressionItem] = []

        anomaly_keys = {(a.domain, a.metric_name, a.department) for a in evaluated_anomalies}

        # Add baseline/signal observations that did not escalate to Anomaly
        for b in baselines:
            if (b.domain, b.metric_name, b.department) in anomaly_keys:
                continue
            observations.append(
                RiskProgressionItem(
                    item_id=f"obs_{b.domain}_{b.metric_name}_{b.department or 'INST'}",
                    stage="Observation",
                    domain=b.domain,
                    metric_name=b.metric_name,
                    department=b.department,
                    time_period=str(b.latest_period) if b.latest_period else None,
                    summary=(
                        f"{b.metric_label} ({b.department or 'Institution-Wide'}): "
                        f"latest value={b.latest_value} ({b.baseline_status})."
                    ),
                    progression_rationale=(
                        "Remains at 'Observation' stage: metric is within baseline bounds or lacks historical comparison for anomaly elevation."
                    ),
                    significance_score=0.15,
                    evidence_refs=[],
                )
            )

        # Place evaluated anomalies into their respective ladder stages
        for a in evaluated_anomalies:
            item = RiskProgressionItem(
                item_id=a.anomaly_id,
                stage=a.risk_stage,
                domain=a.domain,
                metric_name=a.metric_name,
                department=a.department,
                time_period=str(a.academic_year) if a.academic_year else a.when,
                summary=f"{a.what_changed} {a.by_how_much}",
                progression_rationale=a.significance,
                significance_score=a.composite_significance_score,
                evidence_refs=a.evidence[:2],
            )
            if a.risk_stage == "Observation":
                observations.append(item)
            elif a.risk_stage == "Anomaly":
                anomalies.append(item)
            elif a.risk_stage == "Emerging Risk":
                emerging_risks.append(item)
            elif a.risk_stage == "Institutional Risk":
                institutional_risks.append(item)
            elif a.risk_stage == "Crisis":
                crises.append(item)

        # Determine overall institutional stage without inflating isolated anomalies into crises
        cri = assessment.composite_risk_index
        if crises or (cri >= 0.70 and institutional_risks):
            overall_stage: RiskStage = "Crisis"
        elif institutional_risks or (cri >= 0.55 and len(emerging_risks) >= 2):
            overall_stage = "Institutional Risk"
        elif emerging_risks:
            overall_stage = "Emerging Risk"
        elif anomalies:
            overall_stage = "Anomaly"
        else:
            overall_stage = "Observation"

        stage_counts = {
            "Observation": len(observations),
            "Anomaly": len(anomalies),
            "Emerging Risk": len(emerging_risks),
            "Institutional Risk": len(institutional_risks),
            "Crisis": len(crises),
        }

        rationale = (
            f"Overall institutional risk stage is '{overall_stage}' (deterministic CRI={cri:.3f}). "
            f"Ladder distribution: {len(observations)} Observation(s), {len(anomalies)} localized/single-period Anomaly(ies), "
            f"{len(emerging_risks)} Emerging Risk(s), {len(institutional_risks)} Institutional Risk(s), and {len(crises)} Crisis item(s). "
            f"Single-period or uncorroborated deviations are retained at Anomaly/Emerging Risk rather than inflated to Crisis."
        )

        return RiskProgressionLadder(
            overall_institutional_stage=overall_stage,
            composite_risk_index=cri,
            legacy_risk_level=assessment.risk_level,
            observations=observations,
            anomalies=anomalies,
            emerging_risks=emerging_risks,
            institutional_risks=institutional_risks,
            crises=crises,
            stage_counts=stage_counts,
            stage_rationale=rationale,
        )

    # ------------------------------------------------------------------
    # Step 5: Cross-Signal Intelligence (Correlation vs Causation)
    # ------------------------------------------------------------------
    def build_cross_signal_findings(
        self,
        signals: List[DiscoveredSignal],
        baselines: List[HistoricalBaseline],
        evaluated_anomalies: List[EvaluatedAnomaly],
    ) -> List[CrossSignalFinding]:
        """
        When a significant signal changes, inspect related available signals across connected
        institutional domains. Explicitly distinguish correlation from causation using
        'potential contributing factor'.
        """
        baselines_by_domain: Dict[str, List[HistoricalBaseline]] = {}
        for b in baselines:
            baselines_by_domain.setdefault(b.domain, []).append(b)

        anom_lookup: Dict[Tuple[str, str, Optional[str]], EvaluatedAnomaly] = {
            (a.domain, a.metric_name, a.department): a for a in evaluated_anomalies
        }

        sig_by_key: Dict[Tuple[str, str, Optional[str]], List[DiscoveredSignal]] = {}
        for s in signals:
            if s.value is not None and not s.is_duplicate:
                sig_by_key.setdefault((s.domain, s.metric_name, s.context.department), []).append(s)

        findings: List[CrossSignalFinding] = []
        for primary in evaluated_anomalies:
            related_domains = CROSS_SIGNAL_GRAPH.get(primary.domain, [])
            inspected: List[RelatedSignalInspection] = []
            corroborating_domains: List[str] = []
            missing_domains: List[str] = []
            contributing_factors: List[str] = []

            for r_dom in related_domains:
                dom_baselines = baselines_by_domain.get(r_dom, [])
                # Filter to matching department or institution-wide signals
                relevant_b = [
                    rb
                    for rb in dom_baselines
                    if rb.department is None
                    or primary.department is None
                    or rb.department == primary.department
                ]
                if not relevant_b and dom_baselines:
                    # Fall back to any departmental signal in that domain if institution-wide isn't present
                    relevant_b = dom_baselines[:3]

                if not relevant_b:
                    missing_domains.append(r_dom)
                    continue

                for rb in relevant_b:
                    r_key = (rb.domain, rb.metric_name, rb.department)
                    r_anom = anom_lookup.get(r_key)
                    r_obs = sig_by_key.get(r_key, [])
                    ev_refs = [
                        f"{o.provenance.document}:{o.provenance.spreadsheet_location or o.provenance.page_or_section or o.provenance.format_type}"
                        for o in r_obs[-2:]
                    ]

                    comp_val = rb.baseline_mean if rb.baseline_mean is not None else rb.prior_value
                    rel_chg = (
                        round(_pct_change(comp_val, rb.latest_value), 2)
                        if (comp_val is not None and rb.latest_value is not None)
                        else None
                    )

                    if rb.has_contradictions:
                        rel_status = "CONTRADICTORY_SOURCE"
                        summary = (
                            f"Related signal {rb.domain}.{rb.metric_name} ({rb.department or 'Institution-Wide'}) "
                            f"has conflicting source values in period {rb.latest_period}."
                        )
                    elif r_anom is not None:
                        rel_status = "CORROBORATING_CHANGE"
                        if rb.domain not in corroborating_domains:
                            corroborating_domains.append(rb.domain)
                        summary = (
                            f"Corroborating deterioration in {rb.domain}.{rb.metric_name} "
                            f"({rb.department or 'Institution-Wide'}): {r_anom.by_how_much}"
                        )
                        contributing_factors.append(
                            f"Potential contributing factor: Concurrent unfavorable state in {rb.domain} "
                            f"('{rb.metric_label}' — {r_anom.by_how_much}) co-occurs with {primary.domain}.{primary.metric_name}; "
                            f"treated as a correlated potential contributing factor rather than proven causation."
                        )
                    elif comp_val is not None and rb.latest_value is not None:
                        delta = rb.latest_value - comp_val
                        if _is_unfavorable_delta(delta, rb.polarity) and abs(rel_chg or 0.0) >= 4.0:
                            rel_status = "CORROBORATING_CHANGE"
                            if rb.domain not in corroborating_domains:
                                corroborating_domains.append(rb.domain)
                            summary = (
                                f"Mild unfavorable movement in {rb.domain}.{rb.metric_name} "
                                f"({rb.department or 'Institution-Wide'}): {comp_val:.2f} -> {rb.latest_value:.2f} ({rel_chg:+.1f}%)."
                            )
                            contributing_factors.append(
                                f"Potential contributing factor: Mild unfavorable drift in {rb.domain}.{rb.metric_name} "
                                f"({comp_val:.2f} -> {rb.latest_value:.2f}) may be associated with {primary.domain}.{primary.metric_name}."
                            )
                        elif not _is_unfavorable_delta(delta, rb.polarity) and abs(rel_chg or 0.0) >= 5.0:
                            rel_status = "DIVERGENT_TREND"
                            summary = (
                                f"Divergent favorable trend in {rb.domain}.{rb.metric_name} "
                                f"({rb.department or 'Institution-Wide'}): improved {comp_val:.2f} -> {rb.latest_value:.2f} ({rel_chg:+.1f}%) "
                                f"while {primary.domain}.{primary.metric_name} worsened."
                            )
                        else:
                            rel_status = "STABLE_NO_CHANGE"
                            summary = (
                                f"Related signal {rb.domain}.{rb.metric_name} ({rb.department or 'Institution-Wide'}) "
                                f"remained stable ({rb.latest_value:.2f} vs {comp_val:.2f})."
                            )
                    else:
                        rel_status = "STABLE_NO_CHANGE"
                        summary = (
                            f"Related signal {rb.domain}.{rb.metric_name} ({rb.department or 'Institution-Wide'}) "
                            f"observed at {rb.latest_value} (single period; no trend deviation confirmed)."
                        )

                    inspected.append(
                        RelatedSignalInspection(
                            domain=rb.domain,
                            metric_name=rb.metric_name,
                            metric_label=rb.metric_label,
                            department=rb.department,
                            time_period=str(rb.latest_period) if rb.latest_period else None,
                            observed_value=rb.latest_value,
                            prior_or_baseline_value=comp_val,
                            relative_pct_change=rel_chg,
                            relationship_status=rel_status,  # type: ignore[arg-type]
                            summary=summary,
                            evidence_refs=ev_refs,
                        )
                    )

            if not contributing_factors:
                contributing_factors.append(
                    f"Potential contributing factor: No corroborated internal deterioration was detected among currently ingested "
                    f"related signals for {primary.domain}.{primary.metric_name}; external or uncollected domain factors "
                    f"({', '.join(missing_domains[:4]) or 'none'}) should be investigated."
                )

            findings.append(
                CrossSignalFinding(
                    primary_domain=primary.domain,
                    primary_metric=primary.metric_name,
                    department=primary.department,
                    trigger_summary=f"{primary.what_changed} {primary.by_how_much}",
                    inspected_related_signals=inspected,
                    corroborating_domains=corroborating_domains,
                    uninspected_missing_domains=missing_domains,
                    causal_evidence_present=False,
                    correlation_vs_causation_note=(
                        "Statistical co-occurrence across institutional signals establishes correlation, not direct causation. "
                        "Related domain shifts are reported strictly as potential contributing factors unless direct causal provenance is provided."
                    ),
                    potential_contributing_factors=contributing_factors,
                )
            )

        return findings

    # ------------------------------------------------------------------
    # Step 6: Investigation Objects
    # ------------------------------------------------------------------
    def build_investigations(
        self,
        signals: List[DiscoveredSignal],
        baselines: List[HistoricalBaseline],
        evaluated_anomalies: List[EvaluatedAnomaly],
        cross_signal_findings: List[CrossSignalFinding],
        contradictions: List[Dict[str, Any]],
        dq_issues: List[DataQualityIssue],
    ) -> List[InvestigationObject]:
        """
        Construct an InvestigationObject for every major finding with:
        - finding
        - evidence
        - related_signals
        - potential_contributing_factors
        - alternative_explanations
        - confidence
        - missing_information
        """
        cs_map: Dict[Tuple[str, str, Optional[str]], CrossSignalFinding] = {
            (c.primary_domain, c.primary_metric, c.department): c for c in cross_signal_findings
        }
        b_map: Dict[Tuple[str, str, Optional[str]], HistoricalBaseline] = {
            (b.domain, b.metric_name, b.department): b for b in baselines
        }

        investigations: List[InvestigationObject] = []

        for anom in evaluated_anomalies:
            key = (anom.domain, anom.metric_name, anom.department)
            cs = cs_map.get(key)
            b = b_map.get(key)

            related_sigs = cs.inspected_related_signals if cs else []
            pot_factors = list(cs.potential_contributing_factors) if cs else []

            # Alternative explanations
            alt_explanations = list(
                DOMAIN_ALTERNATIVE_EXPLANATIONS.get(
                    anom.domain,
                    [
                        "External regulatory, market, or demographic shift affecting peer institutions simultaneously.",
                        "Changes in internal measurement methodology or reporting boundary between periods.",
                    ],
                )
            )
            if b and b.has_contradictions:
                alt_explanations.insert(
                    0,
                    "Conflicting source documents exist for this metric; the apparent anomaly may stem from un-reconciled source reporting.",
                )
            if cs and any(r.relationship_status == "DIVERGENT_TREND" for r in cs.inspected_related_signals):
                alt_explanations.append(
                    "Related internal indicators improved over the same period, suggesting a localized or external bottleneck rather than systemic institutional decay."
                )

            # Missing information
            missing_info: List[str] = []
            if b and b.baseline_status != "ESTABLISHED":
                missing_info.append(
                    f"Historical time-series depth for {anom.domain}.{anom.metric_name}: "
                    f"currently {len(b.periods_available)} period(s) available; >= 3 periods needed for a full statistical baseline."
                )
            if cs and cs.uninspected_missing_domains:
                missing_info.append(
                    f"Uncollected related domain signals for cross-signal verification: {', '.join(cs.uninspected_missing_domains)}."
                )
            if b and b.has_contradictions:
                missing_info.append(
                    f"Authoritative source reconciliation for conflicting {anom.domain}.{anom.metric_name} values."
                )

            # Compute grounded confidence score
            conf = 0.78
            conf_notes: List[str] = []
            if b and b.baseline_status == "ESTABLISHED":
                conf += 0.12
                conf_notes.append(f"established {len(b.historical_periods_used)}-period baseline (+0.12)")
            elif b and b.baseline_status == "TWO_PERIOD_COMPARISON_ONLY":
                conf -= 0.06
                conf_notes.append("2-period comparison without multi-year baseline (-0.06)")
            else:
                conf -= 0.18
                conf_notes.append("single-period observation without historical baseline (-0.18)")

            if related_sigs:
                conf += 0.05
                conf_notes.append(f"{len(related_sigs)} related signal(s) inspected (+0.05)")
            if cs and cs.uninspected_missing_domains:
                penalty = min(0.12, 0.03 * len(cs.uninspected_missing_domains))
                conf -= penalty
                conf_notes.append(f"{len(cs.uninspected_missing_domains)} related domain(s) missing (-{penalty:.2f})")
            if b and b.has_contradictions:
                conf -= 0.20
                conf_notes.append("conflicting source values present (-0.20)")

            conf = round(max(0.15, min(0.98, conf)), 2)

            investigations.append(
                InvestigationObject(
                    investigation_id=f"inv_{anom.anomaly_id}",
                    domain=anom.domain,
                    metric_name=anom.metric_name,
                    department=anom.department,
                    risk_stage=anom.risk_stage,
                    finding=f"[{anom.risk_stage}] {anom.what_changed} {anom.by_how_much} ({anom.when})",
                    evidence=anom.evidence,
                    related_signals=related_sigs,
                    potential_contributing_factors=pot_factors,
                    alternative_explanations=alt_explanations,
                    confidence=conf,
                    confidence_rationale=f"Confidence={conf:.2f} derived from: " + "; ".join(conf_notes) + ".",
                    missing_information=missing_info,
                )
            )

        # Also create investigation objects for any contradiction that wasn't already covered in evaluated_anomalies
        covered_contra = {(i.domain, i.metric_name, i.department) for i in investigations}
        for c in contradictions:
            c_key = (c["domain"], c["metric_name"], c["department"])
            if c_key in covered_contra:
                continue
            obs_items = c["conflicting_observations"]
            ev_list = [
                f"[{o['source']} | {o['location']}] value={o['raw_value']} ({o['excerpt']})"
                for o in obs_items
            ]
            vals_str = " vs ".join(f"{o['value']} ({o['source']})" for o in obs_items)
            investigations.append(
                InvestigationObject(
                    investigation_id=f"inv_contra_{c['domain']}_{c['metric_name']}_{c['department'] or 'INST'}",
                    domain=c["domain"],
                    metric_name=c["metric_name"],
                    department=c["department"],
                    risk_stage="Anomaly",
                    finding=(
                        f"[Source Contradiction] Conflicting values reported for {c['domain']}.{c['metric_name']} "
                        f"({c['department'] or 'Institution-Wide'}, period={c['time_period']}): {vals_str}."
                    ),
                    evidence=ev_list,
                    related_signals=[],
                    potential_contributing_factors=[
                        "Potential contributing factor: Unsynchronized departmental vs central administrative reporting snapshots.",
                        "Potential contributing factor: Provisional vs audited post-reconciliation figures across uploaded files.",
                    ],
                    alternative_explanations=[
                        "Different inclusion scopes (e.g., sanctioned vs actual intake, or primary vs supplementary placement rounds) across documents.",
                    ],
                    confidence=0.55,
                    confidence_rationale="Confidence reduced to 0.55 due to direct numerical contradiction across ingested sources.",
                    missing_information=[
                        f"Authoritative signed audit verification reconciling {vals_str} for period {c['time_period']}."
                    ],
                )
            )

        return investigations

    # ------------------------------------------------------------------
    # Step 7: Early Warning Detection
    # ------------------------------------------------------------------
    def build_early_warnings(
        self,
        signals: List[DiscoveredSignal],
        baselines: List[HistoricalBaseline],
        evaluated_anomalies: List[EvaluatedAnomaly],
    ) -> List[EarlyWarningIndicator]:
        """
        Detect progression along the early-warning chain:
        weak_signal (1) -> repeated_anomaly (2) -> cross_signal_confirmation (3) -> emerging_risk (4).
        """
        warnings: List[EarlyWarningIndicator] = []
        anom_by_key = {(a.domain, a.metric_name, a.department): a for a in evaluated_anomalies}
        sig_by_key: Dict[Tuple[str, str, Optional[str]], List[DiscoveredSignal]] = {}
        for s in signals:
            if s.value is not None and not s.is_duplicate:
                sig_by_key.setdefault((s.domain, s.metric_name, s.context.department), []).append(s)

        for b in baselines:
            key = (b.domain, b.metric_name, b.department)
            anom = anom_by_key.get(key)
            obs_list = sig_by_key.get(key, [])
            ev_refs = [
                f"{o.provenance.document}:{o.provenance.spreadsheet_location or o.provenance.page_or_section or o.provenance.format_type}"
                for o in obs_list[-2:]
            ]

            if anom is not None:
                # Determine where this anomaly sits on the 4-stage early warning chain
                chain: List[str] = [
                    f"Stage 1 (weak_signal): Initial unfavorable deviation detected in {b.domain}.{b.metric_name} ({anom.by_how_much})."
                ]
                if anom.persistence_periods >= 2 and anom.cross_signal_confirmation:
                    stage: EarlyWarningStage = "emerging_risk"
                    stage_idx = 4
                    chain.append(
                        f"Stage 2 (repeated_anomaly): Unfavorable trend persisted across {anom.persistence_periods} consecutive periods ({anom.when})."
                    )
                    chain.append(
                        f"Stage 3 (cross_signal_confirmation): Corroborated by related domain signals ({', '.join(anom.confirming_signals[:3])})."
                    )
                    chain.append(
                        f"Stage 4 (emerging_risk): Escalated to active '{anom.risk_stage}' requiring targeted institutional intervention."
                    )
                    triggers = [
                        f"Further deterioration in {b.metric_name} or spread beyond {anom.coverage_scope} will escalate to full Institutional Risk / Crisis."
                    ]
                elif anom.cross_signal_confirmation:
                    stage = "cross_signal_confirmation"
                    stage_idx = 3
                    chain.append(
                        f"Stage 3 (cross_signal_confirmation): Corroborated by concurrent shift in related signals ({', '.join(anom.confirming_signals[:3])})."
                    )
                    triggers = [
                        f"Persistence into a second consecutive period for {b.domain}.{b.metric_name} will escalate to Stage 4 (emerging_risk)."
                    ]
                elif anom.persistence_periods >= 2:
                    stage = "repeated_anomaly"
                    stage_idx = 2
                    chain.append(
                        f"Stage 2 (repeated_anomaly): Persisted across {anom.persistence_periods} consecutive periods ({anom.when})."
                    )
                    triggers = [
                        f"Corroborating deterioration in connected domains ({', '.join(CROSS_SIGNAL_GRAPH.get(b.domain, [])[:3])}) will advance to Stage 3 (cross_signal_confirmation) and Stage 4 (emerging_risk)."
                    ]
                else:
                    stage = "weak_signal"
                    stage_idx = 1
                    triggers = [
                        f"A second consecutive unfavorable period in {b.domain}.{b.metric_name} will advance to Stage 2 (repeated_anomaly)."
                    ]

                warnings.append(
                    EarlyWarningIndicator(
                        warning_id=f"ew_{b.domain}_{b.metric_name}_{b.department or 'INST'}",
                        current_stage=stage,
                        stage_index=stage_idx,
                        domain=b.domain,
                        metric_name=b.metric_name,
                        department=b.department,
                        latest_period=str(b.latest_period) if b.latest_period else None,
                        summary=f"[{stage.upper()}] {anom.what_changed} {anom.by_how_much}",
                        progression_chain=chain,
                        confirming_related_signals=anom.confirming_signals,
                        escalation_triggers=triggers,
                        evidence_refs=ev_refs,
                    )
                )
            else:
                # Check if a non-anomalous metric is exhibiting a subtle Weak Signal (early drift 4%..10% or 1.0 <= |Z| < 1.8)
                comp_val = b.baseline_mean if b.baseline_mean is not None else b.prior_value
                if comp_val is not None and b.latest_value is not None:
                    unfav_pct = _unfavorable_magnitude_pct(comp_val, b.latest_value, b.polarity, b.unit)
                    if 4.0 <= unfav_pct < 10.0:
                        warnings.append(
                            EarlyWarningIndicator(
                                warning_id=f"ew_weak_{b.domain}_{b.metric_name}_{b.department or 'INST'}",
                                current_stage="weak_signal",
                                stage_index=1,
                                domain=b.domain,
                                metric_name=b.metric_name,
                                department=b.department,
                                latest_period=str(b.latest_period) if b.latest_period else None,
                                summary=(
                                    f"[WEAK_SIGNAL] Early mild drift in {b.metric_label} "
                                    f"({b.department or 'Institution-Wide'}): {comp_val:.2f} -> {b.latest_value:.2f} "
                                    f"({unfav_pct:.1f}% unfavorable shift, below full anomaly threshold)."
                                ),
                                progression_chain=[
                                    f"Stage 1 (weak_signal): Mild unfavorable drift ({unfav_pct:.1f}%) detected in AY {b.latest_period} prior to full anomaly breach."
                                ],
                                confirming_related_signals=[],
                                escalation_triggers=[
                                    f"Unfavorable shift >= 10% or continuation in next academic period will advance to Stage 2 (repeated_anomaly)."
                                ],
                                evidence_refs=ev_refs,
                            )
                        )

        warnings.sort(key=lambda w: w.stage_index, reverse=True)
        return warnings

    # ------------------------------------------------------------------
    # Step 8: What-Changed Analysis (State Delta Engine)
    # ------------------------------------------------------------------
    def build_what_changed(
        self,
        institution_id: str,
        cet_history: List[CETRankingSignal],
        admissions_history: List[AdmissionsSignal],
        placements_history: List[PlacementsSignal],
        signals: List[DiscoveredSignal],
        baselines: List[HistoricalBaseline],
        evaluated_anomalies: List[EvaluatedAnomaly],
        current_assessment: CrisisAssessment,
        previous_assessments: List[CrisisAssessment],
    ) -> WhatChangedReport:
        """
        Compare current state with previous state:
        - new_signals
        - worsening_signals
        - improving_signals
        - resolved_risks
        - new_risks
        - changed_relationships
        - changed_forecasts
        """
        all_years = sorted(
            {s.context.academic_year for s in signals if s.context.academic_year is not None}
        )

        new_signals: List[SignalChangeDelta] = []
        worsening_signals: List[SignalChangeDelta] = []
        improving_signals: List[SignalChangeDelta] = []
        resolved_risks: List[RiskChangeDelta] = []
        new_risks: List[RiskChangeDelta] = []
        changed_relationships: List[RelationshipChangeDelta] = []
        changed_forecasts: List[ForecastChangeDelta] = []

        if len(all_years) < 2 and len(previous_assessments) < 2:
            # Single-period state: report all ingested signals as initial baseline observations
            for b in baselines:
                new_signals.append(
                    SignalChangeDelta(
                        domain=b.domain,
                        metric_name=b.metric_name,
                        metric_label=b.metric_label,
                        department=b.department,
                        previous_period=None,
                        current_period=str(b.latest_period) if b.latest_period else "Initial",
                        previous_value=None,
                        current_value=b.latest_value,
                        direction="NEW",
                        summary=f"Initial signal observation ingested for {b.metric_label} ({b.department or 'Institution-Wide'}): {b.latest_value}.",
                    )
                )
            for a in evaluated_anomalies:
                new_risks.append(
                    RiskChangeDelta(
                        domain=a.domain,
                        metric_name=a.metric_name,
                        department=a.department,
                        change_type="NEW_RISK",
                        previous_stage="None",
                        current_stage=a.risk_stage,
                        summary=f"Initial {a.risk_stage} detected in {a.domain}.{a.metric_name}: {a.by_how_much}",
                    )
                )
            return WhatChangedReport(
                comparison_available=False,
                comparison_mode="INITIAL_SINGLE_STATE",
                previous_state_label="No prior historical period or assessment snapshot",
                current_state_label=f"AY {all_years[-1]}" if all_years else "Current Snapshot",
                new_signals=new_signals,
                worsening_signals=[],
                improving_signals=[],
                resolved_risks=[],
                new_risks=new_risks,
                changed_relationships=[],
                changed_forecasts=[],
                summary=(
                    f"Initial institutional state recorded ({len(new_signals)} new signal series, "
                    f"{len(new_risks)} active risk finding(s)). At least 2 time periods or 2 assessment snapshots "
                    f"are required for longitudinal delta comparison."
                ),
            )

        curr_yr = all_years[-1] if all_years else None
        prev_yr = all_years[-2] if len(all_years) >= 2 else None

        # Index signals by (domain, metric_name, dept) -> {year: value}
        series_map: Dict[Tuple[str, str, Optional[str]], Dict[int, DiscoveredSignal]] = {}
        for s in signals:
            if s.value is None or s.is_duplicate or s.context.academic_year is None:
                continue
            k = (s.domain, s.metric_name, s.context.department)
            series_map.setdefault(k, {})[s.context.academic_year] = s

        anom_keys = {(a.domain, a.metric_name, a.department): a for a in evaluated_anomalies}

        for b in baselines:
            k = (b.domain, b.metric_name, b.department)
            yr_dict = series_map.get(k, {})
            yrs_present = sorted(yr_dict.keys())

            if not yrs_present:
                continue

            # If this metric only appeared in curr_yr and never existed in any earlier year
            if len(yrs_present) == 1 and curr_yr is not None and yrs_present[0] == curr_yr and len(all_years) >= 2:
                obs = yr_dict[curr_yr]
                new_signals.append(
                    SignalChangeDelta(
                        domain=b.domain,
                        metric_name=b.metric_name,
                        metric_label=b.metric_label,
                        department=b.department,
                        previous_period=str(prev_yr),
                        current_period=str(curr_yr),
                        previous_value=None,
                        current_value=b.latest_value,
                        direction="NEW",
                        summary=(
                            f"New signal introduced in AY {curr_yr}: {b.metric_label} "
                            f"({b.department or 'Institution-Wide'}) = {b.latest_value}."
                        ),
                        evidence_refs=[obs.provenance.document],
                    )
                )
                if k in anom_keys:
                    new_risks.append(
                        RiskChangeDelta(
                            domain=b.domain,
                            metric_name=b.metric_name,
                            department=b.department,
                            change_type="NEW_RISK",
                            previous_stage="Not tracked in prior period",
                            current_stage=anom_keys[k].risk_stage,
                            summary=f"Newly ingested signal {b.domain}.{b.metric_name} entered '{anom_keys[k].risk_stage}' in AY {curr_yr}.",
                        )
                    )
                continue

            if len(yrs_present) >= 2:
                y_curr = yrs_present[-1]
                y_prev = yrs_present[-2]
                obs_curr = yr_dict[y_curr]
                obs_prev = yr_dict[y_prev]
                v_curr = float(obs_curr.value)
                v_prev = float(obs_prev.value)
                delta = round(v_curr - v_prev, 4)
                pct = round(_pct_change(v_prev, v_curr), 2)

                if abs(delta) > 1e-5:
                    is_worsening = _is_unfavorable_delta(delta, b.polarity)
                    if is_worsening:
                        worsening_signals.append(
                            SignalChangeDelta(
                                domain=b.domain,
                                metric_name=b.metric_name,
                                metric_label=b.metric_label,
                                department=b.department,
                                previous_period=str(y_prev),
                                current_period=str(y_curr),
                                previous_value=v_prev,
                                current_value=v_curr,
                                absolute_delta=delta,
                                relative_pct_change=pct,
                                direction="WORSENING",
                                summary=(
                                    f"{b.metric_label} ({b.department or 'Institution-Wide'}) worsened from "
                                    f"{v_prev:.2f} (AY {y_prev}) to {v_curr:.2f} (AY {y_curr}) [{pct:+.1f}%]."
                                ),
                                evidence_refs=[obs_prev.provenance.document, obs_curr.provenance.document],
                            )
                        )
                    elif b.polarity in ("higher_is_better", "lower_is_better"):
                        improving_signals.append(
                            SignalChangeDelta(
                                domain=b.domain,
                                metric_name=b.metric_name,
                                metric_label=b.metric_label,
                                department=b.department,
                                previous_period=str(y_prev),
                                current_period=str(y_curr),
                                previous_value=v_prev,
                                current_value=v_curr,
                                absolute_delta=delta,
                                relative_pct_change=pct,
                                direction="IMPROVING",
                                summary=(
                                    f"{b.metric_label} ({b.department or 'Institution-Wide'}) improved from "
                                    f"{v_prev:.2f} (AY {y_prev}) to {v_curr:.2f} (AY {y_curr}) [{pct:+.1f}%]."
                                ),
                                evidence_refs=[obs_prev.provenance.document, obs_curr.provenance.document],
                            )
                        )

                # Check risk state transition between y_prev and y_curr
                prev_was_risky = False
                if (obs_prev.risk_contribution or 0.0) >= 0.45:
                    prev_was_risky = True
                elif len(yrs_present) >= 3:
                    y_prev2 = yrs_present[-3]
                    v_prev2 = float(yr_dict[y_prev2].value)
                    if _unfavorable_magnitude_pct(v_prev2, v_prev, b.polarity, b.unit) >= 12.0:
                        prev_was_risky = True

                curr_is_risky = k in anom_keys

                if prev_was_risky and not curr_is_risky:
                    resolved_risks.append(
                        RiskChangeDelta(
                            domain=b.domain,
                            metric_name=b.metric_name,
                            department=b.department,
                            change_type="RESOLVED_RISK",
                            previous_stage="Anomaly / Risk",
                            current_stage="Observation (Healthy/Recovered)",
                            summary=(
                                f"Risk resolved in {b.metric_label} ({b.department or 'Institution-Wide'}): "
                                f"recovered from {v_prev:.2f} (AY {y_prev}) to {v_curr:.2f} (AY {y_curr})."
                            ),
                        )
                    )
                elif not prev_was_risky and curr_is_risky:
                    new_risks.append(
                        RiskChangeDelta(
                            domain=b.domain,
                            metric_name=b.metric_name,
                            department=b.department,
                            change_type="NEW_RISK",
                            previous_stage="Observation (Healthy)",
                            current_stage=anom_keys[k].risk_stage,
                            summary=(
                                f"New {anom_keys[k].risk_stage} emerged in {b.metric_label} "
                                f"({b.department or 'Institution-Wide'}) in AY {y_curr}: {v_prev:.2f} -> {v_curr:.2f}."
                            ),
                        )
                    )
                elif prev_was_risky and curr_is_risky and anom_keys[k].persistence_periods >= 2:
                    new_risks.append(
                        RiskChangeDelta(
                            domain=b.domain,
                            metric_name=b.metric_name,
                            department=b.department,
                            change_type="ESCALATED_RISK",
                            previous_stage="Anomaly",
                            current_stage=anom_keys[k].risk_stage,
                            summary=(
                                f"Persistent deterioration escalated {b.metric_label} "
                                f"({b.department or 'Institution-Wide'}) to '{anom_keys[k].risk_stage}' in AY {y_curr}."
                            ),
                        )
                    )

        # Detect changed relationships between connected domain signals
        changed_relationships = self._detect_changed_relationships(series_map, baselines)

        # Detect changed forecasts (prior state vs current state)
        changed_forecasts = self._detect_changed_forecasts(
            institution_id=institution_id,
            cet_history=cet_history,
            admissions_history=admissions_history,
            placements_history=placements_history,
            signals=signals,
            current_assessment=current_assessment,
            previous_assessments=previous_assessments,
            all_years=all_years,
        )

        prev_label = f"AY {prev_yr}" if prev_yr is not None else "Previous Snapshot"
        curr_label = f"AY {curr_yr}" if curr_yr is not None else "Current Snapshot"

        summary = (
            f"State comparison ({prev_label} -> {curr_label}): "
            f"{len(new_signals)} new signal(s), {len(worsening_signals)} worsening signal(s), "
            f"{len(improving_signals)} improving signal(s), {len(resolved_risks)} resolved risk(s), "
            f"{len(new_risks)} new/escalated risk(s), {len(changed_relationships)} changed cross-signal relationship(s), "
            f"and {len(changed_forecasts)} forecast update(s)."
        )

        return WhatChangedReport(
            comparison_available=True,
            comparison_mode="LONGITUDINAL_STATE_COMPARISON",
            previous_state_label=prev_label,
            current_state_label=curr_label,
            new_signals=new_signals,
            worsening_signals=worsening_signals,
            improving_signals=improving_signals,
            resolved_risks=resolved_risks,
            new_risks=new_risks,
            changed_relationships=changed_relationships,
            changed_forecasts=changed_forecasts,
            summary=summary,
        )

    def _detect_changed_relationships(
        self,
        series_map: Dict[Tuple[str, str, Optional[str]], Dict[int, DiscoveredSignal]],
        baselines: List[HistoricalBaseline],
    ) -> List[RelationshipChangeDelta]:
        """Detect shifts in cross-signal relationships (e.g., alignment turning into divergence or co-deterioration)."""
        changes: List[RelationshipChangeDelta] = []
        b_list = [b for b in baselines if len(b.periods_available) >= 2]

        for i, b1 in enumerate(b_list):
            for b2 in b_list[i + 1 :]:
                if b1.domain == b2.domain:
                    continue
                if b1.department != b2.department and b1.department is not None and b2.department is not None:
                    continue
                if b2.domain not in CROSS_SIGNAL_GRAPH.get(b1.domain, []):
                    continue

                s1 = series_map.get((b1.domain, b1.metric_name, b1.department), {})
                s2 = series_map.get((b2.domain, b2.metric_name, b2.department), {})
                common_yrs = sorted(set(s1.keys()).intersection(set(s2.keys())))
                if len(common_yrs) < 2:
                    continue

                y_curr = common_yrs[-1]
                y_prev = common_yrs[-2]
                d1_curr = float(s1[y_curr].value) - float(s1[y_prev].value)
                d2_curr = float(s2[y_curr].value) - float(s2[y_prev].value)
                u1_curr = _is_unfavorable_delta(d1_curr, b1.polarity)
                u2_curr = _is_unfavorable_delta(d2_curr, b2.polarity)

                if len(common_yrs) >= 3:
                    y_prev2 = common_yrs[-3]
                    d1_prev = float(s1[y_prev].value) - float(s1[y_prev2].value)
                    d2_prev = float(s2[y_prev].value) - float(s2[y_prev2].value)
                    u1_prev = _is_unfavorable_delta(d1_prev, b1.polarity)
                    u2_prev = _is_unfavorable_delta(d2_prev, b2.polarity)

                    prev_rel = (
                        "Co-deteriorating"
                        if (u1_prev and u2_prev)
                        else ("Co-improving/Stable" if (not u1_prev and not u2_prev) else "Divergent")
                    )
                    curr_rel = (
                        "Co-deteriorating"
                        if (u1_curr and u2_curr)
                        else ("Co-improving/Stable" if (not u1_curr and not u2_curr) else "Divergent")
                    )
                    if prev_rel != curr_rel:
                        changes.append(
                            RelationshipChangeDelta(
                                domain_a=b1.domain,
                                metric_a=b1.metric_name,
                                domain_b=b2.domain,
                                metric_b=b2.metric_name,
                                department=b1.department or b2.department,
                                previous_relationship=f"{prev_rel} ({y_prev2}->{y_prev})",
                                current_relationship=f"{curr_rel} ({y_prev}->{y_curr})",
                                summary=(
                                    f"Cross-signal relationship between {b1.domain}.{b1.metric_name} and "
                                    f"{b2.domain}.{b2.metric_name} ({b1.department or b2.department or 'Institution-Wide'}) "
                                    f"shifted from '{prev_rel}' ({y_prev2}->{y_prev}) to '{curr_rel}' ({y_prev}->{y_curr})."
                                ),
                            )
                        )
                else:
                    # 2 common years: report if one worsened significantly while the other improved/stayed stable, or if both co-deteriorated
                    if u1_curr != u2_curr:
                        changes.append(
                            RelationshipChangeDelta(
                                domain_a=b1.domain,
                                metric_a=b1.metric_name,
                                domain_b=b2.domain,
                                metric_b=b2.metric_name,
                                department=b1.department or b2.department,
                                previous_relationship=f"Baseline state (AY {y_prev})",
                                current_relationship=f"Divergent movement (AY {y_prev}->{y_curr})",
                                summary=(
                                    f"Divergent movement between {b1.domain}.{b1.metric_name} and "
                                    f"{b2.domain}.{b2.metric_name} across {y_prev}->{y_curr}."
                                ),
                            )
                        )
                    elif u1_curr and u2_curr:
                        changes.append(
                            RelationshipChangeDelta(
                                domain_a=b1.domain,
                                metric_a=b1.metric_name,
                                domain_b=b2.domain,
                                metric_b=b2.metric_name,
                                department=b1.department or b2.department,
                                previous_relationship=f"Prior baseline (AY {y_prev})",
                                current_relationship=f"Coupled co-deterioration (AY {y_prev}->{y_curr})",
                                summary=(
                                    f"Coupled deterioration emerged between {b1.domain}.{b1.metric_name} and "
                                    f"{b2.domain}.{b2.metric_name} across {y_prev}->{y_curr}."
                                ),
                            )
                        )

        return changes[:15]

    def _detect_changed_forecasts(
        self,
        institution_id: str,
        cet_history: List[CETRankingSignal],
        admissions_history: List[AdmissionsSignal],
        placements_history: List[PlacementsSignal],
        signals: List[DiscoveredSignal],
        current_assessment: CrisisAssessment,
        previous_assessments: List[CrisisAssessment],
        all_years: List[int],
    ) -> List[ForecastChangeDelta]:
        """Compare 3-year CRI trajectory forecast between prior state/period and current state/period."""
        # Current 3-year trajectory
        curr_slopes = self._compute_slopes_for_subset(cet_history, admissions_history, placements_history, signals)
        curr_traj = self.predictor.predict_trajectory(
            current_cri=current_assessment.composite_risk_index,
            feature_slopes=curr_slopes,
            years_forward=3,
        )
        curr_y3 = curr_traj[-1].projected_cri if curr_traj else current_assessment.composite_risk_index

        # Prior state CRI & trajectory (slice out latest year if >= 2 years exist)
        if len(all_years) >= 2:
            cutoff_yr = all_years[-2]
            prev_cet = [c for c in cet_history if c.academic_year <= cutoff_yr]
            prev_adm = [a for a in admissions_history if a.academic_year <= cutoff_yr]
            prev_plc = [p for p in placements_history if p.graduation_year <= cutoff_yr]
            prev_dyn = [
                s for s in signals if s.context.academic_year is not None and s.context.academic_year <= cutoff_yr
            ]
            if prev_cet or prev_adm or prev_plc or prev_dyn:
                prev_assess = self.crisis_engine.evaluate_institution(
                    institution_id=institution_id,
                    cet_history=prev_cet,
                    admissions_history=prev_adm,
                    placements_history=prev_plc,
                    dynamic_signals=prev_dyn,
                )
                prev_slopes = self._compute_slopes_for_subset(prev_cet, prev_adm, prev_plc, prev_dyn)
                prev_traj = self.predictor.predict_trajectory(
                    current_cri=prev_assess.composite_risk_index,
                    feature_slopes=prev_slopes,
                    years_forward=3,
                )
                prev_cri = prev_assess.composite_risk_index
                prev_y3 = prev_traj[-1].projected_cri if prev_traj else prev_cri
                proj_delta = round(curr_y3 - prev_y3, 4)
                if proj_delta > 0.02:
                    direction: str = "WORSENED_OUTLOOK"
                elif proj_delta < -0.02:
                    direction = "IMPROVED_OUTLOOK"
                else:
                    direction = "STABLE_OUTLOOK"

                return [
                    ForecastChangeDelta(
                        metric_or_index="composite_risk_index_3yr_forecast",
                        previous_current_cri=prev_cri,
                        new_current_cri=current_assessment.composite_risk_index,
                        previous_year3_projected_cri=prev_y3,
                        new_year3_projected_cri=curr_y3,
                        projected_delta=proj_delta,
                        direction=direction,  # type: ignore[arg-type]
                        summary=(
                            f"3-year projected CRI shifted from {prev_y3:.3f} (as of AY {cutoff_yr}, base CRI={prev_cri:.3f}) "
                            f"to {curr_y3:.3f} (as of AY {all_years[-1]}, base CRI={current_assessment.composite_risk_index:.3f}) "
                            f"[delta: {proj_delta:+.3f}, {direction}]."
                        ),
                    )
                ]

        return []

    @staticmethod
    def _compute_slopes_for_subset(
        cet_history: List[CETRankingSignal],
        admissions_history: List[AdmissionsSignal],
        placements_history: List[PlacementsSignal],
        signals: List[DiscoveredSignal],
    ) -> Dict[str, float]:
        dept_features = extract_institutional_features(cet_history, admissions_history, placements_history)
        if dept_features:
            if len(dept_features) == 1:
                fv = next(iter(dept_features.values()))
                return {
                    "vacancy_rate_slope": fv.vacancy_rate_slope,
                    "placement_pct_slope": fv.placement_pct_slope,
                    "closing_rank_slope": round(fv.closing_rank_slope / 1000.0, 4),
                }
            total_intake = 0.0
            vac_sum = 0.0
            plc_sum = 0.0
            rnk_sum = 0.0
            for dept_name, fv in dept_features.items():
                d_adm = [s for s in admissions_history if s.department == dept_name]
                w = float(sorted(d_adm, key=lambda s: s.academic_year)[-1].sanctioned_intake) if d_adm else 60.0
                total_intake += w
                vac_sum += fv.vacancy_rate_slope * w
                plc_sum += fv.placement_pct_slope * w
                rnk_sum += (fv.closing_rank_slope / 1000.0) * w
            if total_intake > 0:
                return {
                    "vacancy_rate_slope": round(vac_sum / total_intake, 6),
                    "placement_pct_slope": round(plc_sum / total_intake, 4),
                    "closing_rank_slope": round(rnk_sum / total_intake, 4),
                }

        # Fallback to dynamic signal risk_contribution slope across years
        by_yr: Dict[int, List[float]] = {}
        for s in signals:
            if s.context.academic_year is not None and s.risk_contribution is not None:
                by_yr.setdefault(s.context.academic_year, []).append(s.risk_contribution)
        if len(by_yr) >= 2:
            yrs = sorted(by_yr.keys())
            means = [sum(by_yr[y]) / len(by_yr[y]) for y in yrs]
            r_slope = _linear_slope(means)
            return {
                "vacancy_rate_slope": round(r_slope, 4),
                "placement_pct_slope": round(-r_slope * 20.0, 4),
                "closing_rank_slope": 0.0,
            }
        return {"vacancy_rate_slope": 0.0, "placement_pct_slope": 0.0, "closing_rank_slope": 0.0}

    # ------------------------------------------------------------------
    # Step 9: Explicit Epistemic Separation Model
    # ------------------------------------------------------------------
    def build_epistemic_model(
        self,
        institution_id: str,
        signals: List[DiscoveredSignal],
        baselines: List[HistoricalBaseline],
        evaluated_anomalies: List[EvaluatedAnomaly],
        cross_signal_findings: List[CrossSignalFinding],
        investigations: List[InvestigationObject],
        what_changed: WhatChangedReport,
        current_assessment: CrisisAssessment,
        cet_history: List[CETRankingSignal],
        admissions_history: List[AdmissionsSignal],
        placements_history: List[PlacementsSignal],
        contradictions: List[Dict[str, Any]],
        dq_issues: List[DataQualityIssue],
    ) -> EpistemicIntelligenceModel:
        """
        Enforce strict separation between:
        1. OBSERVED_FACT (direct source measurements + verbatim provenance)
        2. ANALYSIS (deterministic math: baselines, Z-scores, slopes, CRI, persistence, coverage)
        3. INFERENCE (cross-signal correlations, potential contributing factors, alternative explanations)
        4. PREDICTION (autoregressive trajectory projections & confidence bands)
        5. RECOMMENDATION (actionable mitigations grounded in findings)
        6. UNKNOWN_INSUFFICIENT_EVIDENCE (missing baselines, uncollected domains, OCR uncertainty, contradictions)
        """
        observed_facts: List[EpistemicStatement] = []
        analyses: List[EpistemicStatement] = []
        inferences: List[EpistemicStatement] = []
        predictions: List[EpistemicStatement] = []
        recommendations: List[EpistemicStatement] = []
        unknowns: List[EpistemicStatement] = []

        # 1. OBSERVED_FACT: Direct observations from source files
        for idx, s in enumerate(signals[:40], start=1):
            if s.is_duplicate:
                continue
            loc = s.provenance.spreadsheet_location or s.provenance.page_or_section or s.provenance.format_type
            observed_facts.append(
                EpistemicStatement(
                    statement_id=f"fact_{idx}_{s.signal_id}",
                    epistemic_type="OBSERVED_FACT",
                    domain=s.domain,
                    metric_name=s.metric_name,
                    department=s.context.department,
                    time_period=str(s.context.academic_year) if s.context.academic_year else s.context.time_period,
                    statement=(
                        f"Observed {s.metric_label} ({s.domain}.{s.metric_name}) = {s.raw_value} "
                        f"in {s.context.department or 'Institution-Wide'} "
                        f"for period {s.context.academic_year or s.context.time_period or 'unspecified'}."
                    ),
                    comparison_or_basis="Direct source measurement (no baseline or causal interpretation applied).",
                    evidence_refs=[f"{s.provenance.document} ({loc}): {s.provenance.excerpt_or_reference}"],
                    confidence=s.provenance.extraction_confidence,
                )
            )

        # 2. ANALYSIS: Deterministic baselines, anomalies, and CRI computation
        analyses.append(
            EpistemicStatement(
                statement_id=f"analysis_cri_{institution_id}",
                epistemic_type="ANALYSIS",
                domain="general_institutional",
                metric_name="composite_risk_index",
                statement=(
                    f"Deterministic Composite Risk Index (CRI) computed at {current_assessment.composite_risk_index:.3f} "
                    f"(primary driver: {current_assessment.primary_driving_signal})."
                ),
                comparison_or_basis="Weighted mathematical aggregation of institutional signal histories.",
                evidence_refs=[f"CrisisIntelligenceEngine({institution_id})"],
                confidence=current_assessment.confidence_score,
            )
        )

        for b in baselines:
            if b.baseline_status == "ESTABLISHED":
                analyses.append(
                    EpistemicStatement(
                        statement_id=f"analysis_base_{b.domain}_{b.metric_name}_{b.department or 'INST'}",
                        epistemic_type="ANALYSIS",
                        domain=b.domain,
                        metric_name=b.metric_name,
                        department=b.department,
                        time_period=str(b.latest_period),
                        statement=b.explanation,
                        comparison_or_basis=f"Historical periods {b.historical_periods_used}",
                        evidence_refs=[],
                        confidence=0.95,
                    )
                )

        for a in evaluated_anomalies:
            analyses.append(
                EpistemicStatement(
                    statement_id=f"analysis_{a.anomaly_id}",
                    epistemic_type="ANALYSIS",
                    domain=a.domain,
                    metric_name=a.metric_name,
                    department=a.department,
                    time_period=str(a.academic_year) if a.academic_year else a.when,
                    statement=f"{a.what_changed} {a.by_how_much} {a.significance}",
                    comparison_or_basis=a.comparison_basis,
                    evidence_refs=a.evidence[:2],
                    confidence=0.92 if a.historical_deviation_zscore is not None else 0.80,
                )
            )

        # 3. INFERENCE: Cross-signal potential contributing factors & alternative explanations
        for inv in investigations:
            for pf in inv.potential_contributing_factors:
                inferences.append(
                    EpistemicStatement(
                        statement_id=f"inf_pf_{inv.investigation_id}_{len(inferences)+1}",
                        epistemic_type="INFERENCE",
                        domain=inv.domain,
                        metric_name=inv.metric_name,
                        department=inv.department,
                        statement=pf,
                        comparison_or_basis="Cross-signal correlation analysis (explicitly non-causal; potential contributing factor).",
                        evidence_refs=inv.evidence[:1],
                        confidence=inv.confidence,
                    )
                )
            if inv.alternative_explanations:
                inferences.append(
                    EpistemicStatement(
                        statement_id=f"inf_alt_{inv.investigation_id}",
                        epistemic_type="INFERENCE",
                        domain=inv.domain,
                        metric_name=inv.metric_name,
                        department=inv.department,
                        statement=f"Alternative explanation for {inv.domain}.{inv.metric_name}: {inv.alternative_explanations[0]}",
                        comparison_or_basis="Competing hypothesis generation to prevent premature causal attribution.",
                        evidence_refs=inv.evidence[:1],
                        confidence=round(max(0.30, inv.confidence - 0.15), 2),
                    )
                )

        # 4. PREDICTION: 3-year autoregressive trajectory forecast
        slopes = self._compute_slopes_for_subset(cet_history, admissions_history, placements_history, signals)
        sq_traj = self.predictor.predict_trajectory(
            current_cri=current_assessment.composite_risk_index,
            feature_slopes=slopes,
            years_forward=3,
        )
        for pt in sq_traj:
            predictions.append(
                EpistemicStatement(
                    statement_id=f"pred_cri_y{pt.year_offset}",
                    epistemic_type="PREDICTION",
                    domain="general_institutional",
                    metric_name="projected_cri",
                    time_period=f"T+{pt.year_offset}Y",
                    statement=(
                        f"Status-quo projection at Year +{pt.year_offset}: projected CRI = {pt.projected_cri:.3f} "
                        f"(confidence band: [{pt.confidence_band_low:.3f}, {pt.confidence_band_high:.3f}])."
                    ),
                    comparison_or_basis=f"Autoregressive trajectory model from current CRI={current_assessment.composite_risk_index:.3f} and slopes={slopes}.",
                    evidence_refs=[f"TrajectoryPredictor(T+{pt.year_offset})"],
                    confidence=round(max(0.50, 0.90 - 0.08 * pt.year_offset), 2),
                )
            )

        # 5. RECOMMENDATION: Actionable governance & academic mitigations grounded in findings
        for idx, rec_text in enumerate(current_assessment.recommended_mitigations, start=1):
            recommendations.append(
                EpistemicStatement(
                    statement_id=f"rec_core_{idx}",
                    epistemic_type="RECOMMENDATION",
                    domain="general_institutional",
                    statement=rec_text,
                    comparison_or_basis=f"Grounded in {current_assessment.primary_driving_signal} (CRI={current_assessment.composite_risk_index:.3f}).",
                    evidence_refs=[],
                    confidence=0.85,
                )
            )

        for inv in investigations[:5]:
            recommendations.append(
                EpistemicStatement(
                    statement_id=f"rec_{inv.investigation_id}",
                    epistemic_type="RECOMMENDATION",
                    domain=inv.domain,
                    metric_name=inv.metric_name,
                    department=inv.department,
                    statement=(
                        f"Conduct targeted review of {inv.domain}.{inv.metric_name} "
                        f"({inv.department or 'Institution-Wide'}, stage={inv.risk_stage}) "
                        + (
                            f"and collect missing evidence ({inv.missing_information[0]})"
                            if inv.missing_information
                            else "and monitor related domain indicators"
                        )
                        + "."
                    ),
                    comparison_or_basis=inv.finding,
                    evidence_refs=inv.evidence[:1],
                    confidence=inv.confidence,
                )
            )

        # 6. UNKNOWN_INSUFFICIENT_EVIDENCE: Insufficient baselines, missing related domains, OCR uncertainty, contradictions
        for b in baselines:
            if b.baseline_status in ("INSUFFICIENT_HISTORY", "TWO_PERIOD_COMPARISON_ONLY", "CONTRADICTORY_HISTORY"):
                unknowns.append(
                    EpistemicStatement(
                        statement_id=f"unk_base_{b.domain}_{b.metric_name}_{b.department or 'INST'}",
                        epistemic_type="UNKNOWN_INSUFFICIENT_EVIDENCE",
                        domain=b.domain,
                        metric_name=b.metric_name,
                        department=b.department,
                        statement=b.explanation,
                        comparison_or_basis=f"Baseline status: {b.baseline_status}",
                        evidence_refs=[],
                        confidence=1.0,
                    )
                )

        for cs in cross_signal_findings:
            if cs.uninspected_missing_domains:
                unknowns.append(
                    EpistemicStatement(
                        statement_id=f"unk_cs_{cs.primary_domain}_{cs.primary_metric}_{cs.department or 'INST'}",
                        epistemic_type="UNKNOWN_INSUFFICIENT_EVIDENCE",
                        domain=cs.primary_domain,
                        metric_name=cs.primary_metric,
                        department=cs.department,
                        statement=(
                            f"Insufficient evidence to evaluate related domains ({', '.join(cs.uninspected_missing_domains)}) "
                            f"for {cs.primary_domain}.{cs.primary_metric} ({cs.department or 'Institution-Wide'}); "
                            f"no signals from those domains have been ingested yet."
                        ),
                        comparison_or_basis="Cross-signal domain completeness check",
                        evidence_refs=[],
                        confidence=1.0,
                    )
                )

        for c in contradictions:
            vals_str = " vs ".join(f"{o['value']} ({o['source']})" for o in c["conflicting_observations"])
            unknowns.append(
                EpistemicStatement(
                    statement_id=f"unk_contra_{c['domain']}_{c['metric_name']}_{c['department'] or 'INST'}",
                    epistemic_type="UNKNOWN_INSUFFICIENT_EVIDENCE",
                    domain=c["domain"],
                    metric_name=c["metric_name"],
                    department=c["department"],
                    time_period=c["time_period"],
                    statement=(
                        f"Unresolved contradiction for {c['domain']}.{c['metric_name']} "
                        f"({c['department'] or 'Institution-Wide'}, period={c['time_period']}): "
                        f"conflicting source values {vals_str} remain visible pending audit reconciliation."
                    ),
                    comparison_or_basis="Source contradiction detection",
                    evidence_refs=[o["source"] for o in c["conflicting_observations"]],
                    confidence=1.0,
                )
            )

        for dq in dq_issues:
            if dq.issue_type in ("ocr_uncertainty", "missing_periods", "incomplete_coverage", "ambiguous_values"):
                unknowns.append(
                    EpistemicStatement(
                        statement_id=f"unk_dq_{dq.issue_type}_{len(unknowns)+1}",
                        epistemic_type="UNKNOWN_INSUFFICIENT_EVIDENCE",
                        domain=dq.affected_domain or "general_institutional",
                        metric_name=dq.affected_metric,
                        department=dq.affected_department,
                        time_period=dq.affected_period,
                        statement=f"[{dq.issue_type.upper()}] {dq.description}",
                        comparison_or_basis="Data quality & evidentiary sufficiency audit",
                        evidence_refs=dq.provenance_refs,
                        confidence=1.0,
                    )
                )

        return EpistemicIntelligenceModel(
            observed_facts=observed_facts,
            analyses=analyses,
            inferences=inferences,
            predictions=predictions,
            recommendations=recommendations,
            unknown_and_insufficient_evidence=unknowns,
        )
