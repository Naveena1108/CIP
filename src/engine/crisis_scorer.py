from typing import Dict, List, Optional
from pydantic import BaseModel, Field
from src.contracts import (
    CETRankingSignal,
    AdmissionsSignal,
    PlacementsSignal,
    CrisisAssessment,
    SignalAnomaly,
    DiscoveredSignal,
)
from .anomaly_detector import TemporalAnomalyDetector

class RiskWeightProfile(BaseModel):
    name: str = "DEFAULT_TECHNICAL_COLLEGE"
    weight_admissions: float = 0.40
    weight_placements: float = 0.35
    weight_ranking: float = 0.25

class CrisisIntelligenceEngine:
    """
    Cross-Signal Crisis Intelligence & Risk Scoring Engine.
    Fuses multiple institutional signals into a calibrated Composite Risk Index (CRI)
    with departmental rollup, cross-signal divergence amplification, universal dynamic
    signal discovery across 16+ domains, and sparse-data fallback.
    """
    def __init__(self, profile: Optional[RiskWeightProfile] = None):
        self.profile = profile or RiskWeightProfile()
        self.w_adm = self.profile.weight_admissions
        self.w_plc = self.profile.weight_placements
        self.w_rnk = self.profile.weight_ranking
        self.detector = TemporalAnomalyDetector()

    def evaluate_institution(
        self,
        institution_id: str,
        cet_history: List[CETRankingSignal],
        admissions_history: List[AdmissionsSignal],
        placements_history: List[PlacementsSignal],
        dynamic_signals: Optional[List[DiscoveredSignal]] = None,
    ) -> CrisisAssessment:
        anomalies: List[SignalAnomaly] = []
        anomalies.extend(self.detector.analyze_admissions(admissions_history))
        anomalies.extend(self.detector.analyze_placements(placements_history))
        anomalies.extend(self.detector.analyze_cet_ranking(cet_history))

        non_canonical_dynamic = [
            s for s in (dynamic_signals or [])
            if s.domain not in ("admissions", "placements", "ranking")
        ]
        dynamic_anomalies = self._extract_dynamic_anomalies(dynamic_signals or [])

        # If no canonical signals exist at all, evaluate directly from universal discovered signals
        if not cet_history and not admissions_history and not placements_history and dynamic_signals:
            return self._evaluate_dynamic_signals_only(institution_id, dynamic_signals, dynamic_anomalies)

        # Check for multi-department aggregation
        depts = set(s.department for s in admissions_history)
        if len(depts) > 1:
            assessment = self._evaluate_multi_department_rollup(
                institution_id, depts, cet_history, admissions_history, placements_history, anomalies
            )
        else:
            # Single-stream cross-signal divergence detection
            anomalies.extend(self.detector.analyze_cross_signal(cet_history, admissions_history, placements_history))
            assessment = self._evaluate_single_stream(
                institution_id, cet_history, admissions_history, placements_history, anomalies
            )

        if non_canonical_dynamic and dynamic_anomalies:
            assessment.anomalies_detected.extend(dynamic_anomalies)
        return assessment


    def _evaluate_single_stream(
        self,
        institution_id: str,
        cet_history: List[CETRankingSignal],
        admissions_history: List[AdmissionsSignal],
        placements_history: List[PlacementsSignal],
        anomalies: List[SignalAnomaly]
    ) -> CrisisAssessment:
        adm_threat = 0.0
        if admissions_history:
            latest_adm = sorted(admissions_history, key=lambda s: s.academic_year)[-1]
            adm_threat = min(1.0, latest_adm.vacancy_rate * 1.5)

        plc_threat = 0.0
        if placements_history:
            latest_plc = sorted(placements_history, key=lambda s: s.graduation_year)[-1]
            plc_threat = max(0.0, min(1.0, (100.0 - latest_plc.placement_percentage) / 100.0))

        rnk_threat = 0.0
        if len(cet_history) >= 2:
            sorted_rnk = sorted(cet_history, key=lambda s: s.academic_year)
            rank_growth = (sorted_rnk[-1].closing_rank - sorted_rnk[0].closing_rank) / float(sorted_rnk[0].closing_rank)
            rnk_threat = max(0.0, min(1.0, rank_growth))

        cri = (self.w_adm * adm_threat) + (self.w_plc * plc_threat) + (self.w_rnk * rnk_threat)

        # Non-linear cross-signal and critical shock contagion (top-level evaluations with anomalies)
        has_cross_signal = any("Cross-Signal" in a.signal_name for a in anomalies)
        has_critical_anomaly = any(a.severity == "CRITICAL" for a in anomalies)
        max_component_threat = max(adm_threat, plc_threat, rnk_threat)

        if has_cross_signal:
            cri = max(cri, 0.50 + (0.25 * max_component_threat))
        elif has_critical_anomaly and max_component_threat >= 0.70:
            cri = max(cri, 0.65 * max_component_threat + 0.35 * cri)

        cri = round(min(1.0, max(0.0, cri)), 3)

        if cri >= 0.70:
            risk_level = "CRITICAL"
        elif cri >= 0.50:
            risk_level = "HIGH"
        elif cri >= 0.30:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"

        threat_map = {
            "Admissions Collapse": self.w_adm * adm_threat,
            "Placement Degradation": self.w_plc * plc_threat,
            "Ranking & Cutoff Drift": self.w_rnk * rnk_threat
        }
        if has_cross_signal and adm_threat < 0.25 and plc_threat >= 0.55:
            primary_driver = "Cross-Signal Divergence (Intake vs. Placements)"
        else:
            primary_driver = max(threat_map, key=threat_map.get)

        mitigations = []
        if adm_threat > 0.4:
            mitigations.append("Audit regional CET cutoff competitiveness and restructure intake quotas.")
        if plc_threat > 0.4:
            mitigations.append("Initiate immediate corporate relations outreach and overhaul technical training.")
        if rnk_threat > 0.3:
            mitigations.append("Enhance faculty credentials and state accreditation ranking alignment.")
        if has_cross_signal:
            mitigations.append("Align intake expansion with placement absorption capacity to halt unplaced cohort accumulation.")

        return CrisisAssessment(
            institution_id=institution_id,
            composite_risk_index=cri,
            risk_level=risk_level,
            primary_driving_signal=primary_driver,
            anomalies_detected=anomalies,
            confidence_score=0.92 if len(admissions_history) >= 3 else 0.75,
            executive_summary=f"Institution {institution_id} assessed at {risk_level} risk (CRI: {cri}). Primary threat driver is {primary_driver} with {len(anomalies)} statistical anomalies flagged.",
            recommended_mitigations=mitigations
        )

    def _evaluate_multi_department_rollup(
        self,
        institution_id: str,
        depts: set,
        cet_history: List[CETRankingSignal],
        admissions_history: List[AdmissionsSignal],
        placements_history: List[PlacementsSignal],
        anomalies: List[SignalAnomaly]
    ) -> CrisisAssessment:
        total_intake = 0
        dept_weighted_cri = 0.0
        threat_contributions = {"Admissions": 0.0, "Placements": 0.0, "Ranking": 0.0}
        has_critical_dept = False

        for d in depts:
            d_adm = [s for s in admissions_history if s.department == d]
            d_plc = [s for s in placements_history if s.department == d]
            d_cet = [s for s in cet_history if s.department == d]

            latest_intake = sorted(d_adm, key=lambda s: s.academic_year)[-1].sanctioned_intake if d_adm else 60
            total_intake += latest_intake

            dept_assessment = self._evaluate_single_stream(
                institution_id, d_cet, d_adm, d_plc, []
            )

            dept_weighted_cri += (dept_assessment.composite_risk_index * latest_intake)

            if dept_assessment.risk_level == "CRITICAL" and d in ["CSE", "AIML", "ECE", "MECH"]:
                has_critical_dept = True

            if "Admissions" in dept_assessment.primary_driving_signal:
                threat_contributions["Admissions"] += latest_intake
            elif "Placement" in dept_assessment.primary_driving_signal:
                threat_contributions["Placements"] += latest_intake
            else:
                threat_contributions["Ranking"] += latest_intake

        institution_cri = dept_weighted_cri / float(total_intake) if total_intake > 0 else 0.0
        
        # Apply critical department penalty
        if has_critical_dept:
            institution_cri = min(1.0, institution_cri + 0.05)

        institution_cri = round(institution_cri, 3)

        if institution_cri >= 0.70:
            risk_level = "CRITICAL"
        elif institution_cri >= 0.50:
            risk_level = "HIGH"
        elif institution_cri >= 0.30:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"

        primary_driver = f"{max(threat_contributions, key=threat_contributions.get)} Degradation"

        mitigations = [
            f"Address severe placement contraction across high-intake departments ({', '.join(sorted(list(depts))[:3])}).",
            "Establish corporate advisory board to update technical curriculum and restore hiring pipelines.",
            "Rebalance departmental seat quotas toward market-demanded specializations."
        ]

        return CrisisAssessment(
            institution_id=institution_id,
            composite_risk_index=institution_cri,
            risk_level=risk_level,
            primary_driving_signal=primary_driver,
            anomalies_detected=anomalies,
            confidence_score=0.94,
            executive_summary=f"Institution {institution_id} (Multi-Department Rollup across {len(depts)} programs) assessed at {risk_level} risk (CRI: {institution_cri}). Primary threat driver is {primary_driver} across {total_intake} annual intake capacity.",
            recommended_mitigations=mitigations
        )

    def _extract_dynamic_anomalies(self, dynamic_signals: List[DiscoveredSignal]) -> List[SignalAnomaly]:
        """
        Convert high-risk or contradictory dynamic signals across any institutional domain
        into empirical SignalAnomaly objects for dossier and narrative grounding.
        """
        anomalies: List[SignalAnomaly] = []
        for s in dynamic_signals:
            if s.domain in ("admissions", "placements", "ranking") and not s.is_contradictory:
                continue
            yr = s.context.academic_year or 2025
            if s.is_contradictory and s.value is not None:
                anomalies.append(
                    SignalAnomaly(
                        signal_name=f"Contradictory Source ({s.domain.title()})",
                        academic_year=yr,
                        metric_name=s.metric_name,
                        observed_value=float(s.value),
                        baseline_value=float(s.value),
                        deviation_zscore=-2.5,
                        severity="HIGH",
                        description=(
                            f"Conflicting values reported for {s.metric_label} in {s.provenance.document} "
                            f"({s.provenance.spreadsheet_location or s.provenance.page_or_section or 'source'})."
                        ),
                    )
                )
            elif s.risk_contribution is not None and s.risk_contribution >= 0.45 and s.value is not None:
                sev = "CRITICAL" if s.risk_contribution >= 0.75 else ("HIGH" if s.risk_contribution >= 0.60 else "MEDIUM")
                baseline = 85.0 if s.polarity == "higher_is_better" else 10.0
                z_val = round((float(s.value) - baseline) / 10.0, 2)
                anomalies.append(
                    SignalAnomaly(
                        signal_name=f"{s.domain.title()} Intelligence",
                        academic_year=yr,
                        metric_name=s.metric_name,
                        observed_value=float(s.value),
                        baseline_value=baseline,
                        deviation_zscore=z_val,
                        severity=sev,
                        description=(
                            f"{s.metric_label} observed at {s.raw_value} in {s.provenance.document} "
                            f"({s.provenance.page_or_section or s.provenance.spreadsheet_location or 'source'})."
                        ),
                    )
                )
        return anomalies

    def _evaluate_dynamic_signals_only(
        self,
        institution_id: str,
        dynamic_signals: List[DiscoveredSignal],
        anomalies: List[SignalAnomaly],
    ) -> CrisisAssessment:
        """
        Evaluate an institution when only universal/non-fixed-column signals are present
        (e.g. faculty, finance, research, attendance, retention, grievances, infrastructure, compliance).
        """
        domain_risks: Dict[str, List[float]] = {}
        for s in dynamic_signals:
            if s.risk_contribution is not None:
                domain_risks.setdefault(s.domain, []).append(s.risk_contribution)

        if domain_risks:
            domain_means = {dom: sum(vals) / len(vals) for dom, vals in domain_risks.items()}
            cri = round(min(1.0, max(0.0, sum(domain_means.values()) / len(domain_means))), 3)
            top_domain = max(domain_means, key=domain_means.get)
            primary_driver = f"{top_domain.replace('_', ' ').title()} Domain Risk"
        else:
            cri = 0.22
            primary_driver = "General Institutional Monitoring"

        if cri >= 0.70:
            risk_level = "CRITICAL"
        elif cri >= 0.50:
            risk_level = "HIGH"
        elif cri >= 0.30:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"

        domains_list = sorted({s.domain for s in dynamic_signals})
        mitigations = [
            f"Review discovered indicators in {primary_driver} across {', '.join(domains_list[:4])}.",
            "Reconcile any flagged data quality issues or cross-document contradictions in source records.",
        ]

        mean_conf = (
            round(sum(s.provenance.extraction_confidence for s in dynamic_signals) / len(dynamic_signals), 2)
            if dynamic_signals
            else 0.75
        )
        return CrisisAssessment(
            institution_id=institution_id,
            composite_risk_index=cri,
            risk_level=risk_level,
            primary_driving_signal=primary_driver,
            anomalies_detected=anomalies,
            confidence_score=min(1.0, max(0.5, mean_conf)),
            executive_summary=(
                f"Institution {institution_id} assessed via universal dynamic signal discovery across "
                f"{len(domains_list)} domain(s) ({', '.join(domains_list)}) at {risk_level} risk (CRI: {cri}). "
                f"Primary driver: {primary_driver}."
            ),
            recommended_mitigations=mitigations,
        )

