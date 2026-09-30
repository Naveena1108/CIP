import math
from typing import List, Dict, Any
from src.contracts import (
    CETRankingSignal,
    AdmissionsSignal,
    PlacementsSignal,
    SignalAnomaly
)

class TemporalAnomalyDetector:
    """
    Statistical Time-Series Anomaly Detector.
    Detects acute drops, sudden metric shifts, multi-year decaying trends,
    and cross-signal structural divergence.
    """

    @staticmethod
    def compute_zscore(values: List[float], current_val: float) -> float:
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
        std_dev = math.sqrt(variance)
        if std_dev == 0:
            return 0.0
        return (current_val - mean) / std_dev

    def analyze_admissions(self, history: List[AdmissionsSignal]) -> List[SignalAnomaly]:
        anomalies = []
        if len(history) < 2:
            return anomalies
        
        sorted_signals = sorted(history, key=lambda s: s.academic_year)
        enrolled_history = [float(s.enrolled_count) for s in sorted_signals[:-1]]
        latest = sorted_signals[-1]

        z = self.compute_zscore(enrolled_history, float(latest.enrolled_count))
        
        # In admissions, a negative Z-score with material vacancy (>= 15%) or high absolute vacancy (>= 40%) flags an anomaly
        if (z <= -2.0 and latest.vacancy_rate >= 0.15) or latest.vacancy_rate >= 0.40:
            severity = "CRITICAL" if (z <= -3.0 or latest.vacancy_rate >= 0.55) else "HIGH"
            anomalies.append(SignalAnomaly(
                signal_name="Admissions",
                academic_year=latest.academic_year,
                metric_name="enrolled_count",
                observed_value=float(latest.enrolled_count),
                baseline_value=sum(enrolled_history) / len(enrolled_history),
                deviation_zscore=round(z, 2),
                severity=severity,
                description=f"Severe enrollment drop: {latest.enrolled_count} students enrolled (Vacancy rate {latest.vacancy_rate*100:.1f}%, Z-Score: {z:.2f})"
            ))
        return anomalies

    def analyze_placements(self, history: List[PlacementsSignal]) -> List[SignalAnomaly]:
        anomalies = []
        if len(history) < 2:
            return anomalies
        
        sorted_signals = sorted(history, key=lambda s: s.graduation_year)
        placed_pct_history = [s.placement_percentage for s in sorted_signals[:-1]]
        latest = sorted_signals[-1]

        z = self.compute_zscore(placed_pct_history, latest.placement_percentage)
        
        if (z <= -2.0 and latest.placement_percentage < 75.0) or latest.placement_percentage < 50.0:
            severity = "CRITICAL" if (z <= -3.0 or latest.placement_percentage < 35.0) else "HIGH"
            anomalies.append(SignalAnomaly(
                signal_name="Placements",
                academic_year=latest.graduation_year,
                metric_name="placement_percentage",
                observed_value=latest.placement_percentage,
                baseline_value=sum(placed_pct_history) / len(placed_pct_history),
                deviation_zscore=round(z, 2),
                severity=severity,
                description=f"Placement rate collapse: {latest.placement_percentage:.1f}% placed (Z-Score: {z:.2f})"
            ))
        return anomalies

    def analyze_cet_ranking(self, history: List[CETRankingSignal]) -> List[SignalAnomaly]:
        anomalies = []
        if len(history) < 2:
            return anomalies
        
        sorted_signals = sorted(history, key=lambda s: s.academic_year)
        closing_ranks = [float(s.closing_rank) for s in sorted_signals[:-1]]
        latest = sorted_signals[-1]

        baseline_mean = sum(closing_ranks) / len(closing_ranks)
        relative_slippage = (float(latest.closing_rank) - baseline_mean) / baseline_mean if baseline_mean > 0 else 0.0

        # For ranks, a higher rank number means worse performance (positive Z-score = worse)
        # Require material slippage (>= 15%) alongside statistical Z-score >= 2.0, or severe slippage (>= 50%)
        z = self.compute_zscore(closing_ranks, float(latest.closing_rank))
        
        if (z >= 2.0 and relative_slippage >= 0.15) or relative_slippage >= 0.50:
            severity = "CRITICAL" if (z >= 3.0 and relative_slippage >= 0.30) or relative_slippage >= 0.75 else "HIGH"
            anomalies.append(SignalAnomaly(
                signal_name="CET / Ranking",
                academic_year=latest.academic_year,
                metric_name="closing_rank",
                observed_value=float(latest.closing_rank),
                baseline_value=baseline_mean,
                deviation_zscore=round(z, 2),
                severity=severity,
                description=f"Significant rank deterioration: Cutoff slipped to {latest.closing_rank} (Z-Score: +{z:.2f})"
            ))
        return anomalies

    def analyze_cross_signal(
        self,
        cet_history: List[CETRankingSignal],
        admissions_history: List[AdmissionsSignal],
        placements_history: List[PlacementsSignal]
    ) -> List[SignalAnomaly]:
        """
        Detects structural cross-signal divergence where strong or rising admissions/ranking
        coexist with severe placement deterioration (decoupled institutional signals).
        """
        anomalies: List[SignalAnomaly] = []
        if len(admissions_history) < 2 or len(placements_history) < 2:
            return anomalies

        sorted_adm = sorted(admissions_history, key=lambda s: s.academic_year)
        sorted_plc = sorted(placements_history, key=lambda s: s.graduation_year)

        latest_adm = sorted_adm[-1]
        latest_plc = sorted_plc[-1]

        first_enrolled = float(sorted_adm[0].enrolled_count)
        last_enrolled = float(latest_adm.enrolled_count)
        intake_growth = ((last_enrolled - first_enrolled) / first_enrolled * 100.0) if first_enrolled > 0 else 0.0

        first_placed = float(sorted_plc[0].placed_students)
        last_placed = float(latest_plc.placed_students)
        placed_growth = ((last_placed - first_placed) / first_placed * 100.0) if first_placed > 0 else 0.0

        divergence = round((intake_growth - placed_growth) / 100.0, 4)

        # Cross-signal crisis: high enrollment retention (<20% vacancy) while placements collapse (<45%) and divergence >= 0.40
        if latest_adm.vacancy_rate < 0.20 and latest_plc.placement_percentage < 45.0 and divergence >= 0.40:
            severity = "CRITICAL" if divergence >= 0.60 or latest_plc.placement_percentage < 30.0 else "HIGH"
            anomalies.append(SignalAnomaly(
                signal_name="Cross-Signal Divergence",
                academic_year=latest_plc.graduation_year,
                metric_name="intake_to_placed_divergence",
                observed_value=round(divergence, 2),
                baseline_value=0.0,
                deviation_zscore=round(-abs(divergence) * 4.0, 2),
                severity=severity,
                description=(
                    f"Cross-signal decoupling: High admission fill ({100.0 - latest_adm.vacancy_rate*100:.1f}%) "
                    f"diverges sharply from collapsing placement rate ({latest_plc.placement_percentage:.1f}%, "
                    f"divergence index: +{divergence:.2f})"
                )
            ))
        return anomalies
