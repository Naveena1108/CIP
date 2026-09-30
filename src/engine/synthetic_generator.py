import json
import random
from typing import Dict, List, Any
from datetime import datetime
from src.contracts import (
    ProvenanceMetadata,
    CETRankingSignal,
    AdmissionsSignal,
    PlacementsSignal
)

class SyntheticDataGenerator:
    """
    Generates multi-year parameterized higher education datasets
    simulating diverse institutional trajectories across 10 validation scenarios.
    """

    @staticmethod
    def generate_scenario(
        institution_id: str,
        scenario_type: str = "HEALTHY",
        start_year: int = 2020,
        years_count: int = 5
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Generates raw dictionaries simulating institutional exports across years.
        Supported scenarios:
        1. HEALTHY
        2. ADMISSIONS_CRASH / ADMISSIONS_DECLINE
        3. PLACEMENT_COLLAPSE / PLACEMENT_DETERIORATION
        4. RANKING_DETERIORATION
        5. CROSS_SIGNAL_CRISIS
        6. CASCADING_CRISIS
        7. GRADUAL_DECLINE
        8. SUDDEN_CRISIS
        9. RECOVERY
        10. NOISY_MISSING_DATA
        """
        random.seed(42)  # Deterministic for reproducible tests
        cet_records = []
        admissions_records = []
        placements_records = []

        base_intake = 300
        base_opening_rank = 1500
        base_closing_rank = 5000
        base_eligible = 280

        # For NOISY_MISSING_DATA, simulate a gap year (e.g. missing year index 2) or sparse observation window
        active_indices = list(range(years_count))
        if scenario_type == "NOISY_MISSING_DATA" and years_count >= 4:
            # Drop middle periods to simulate missing/incomplete multi-year records (only 2 sparse years remain)
            active_indices = [0, years_count - 1]

        for i in active_indices:
            year = start_year + i
            opening_rank = base_opening_rank
            closing_rank = base_closing_rank
            percentile = 92.0
            intake = base_intake
            enrolled = int(base_intake * 0.96)
            eligible = base_eligible
            placed = int(eligible * 0.88)
            median_lpa = 8.0
            max_lpa = 20.0

            if scenario_type == "HEALTHY":
                intake = base_intake
                enrolled = int(base_intake * random.uniform(0.95, 0.99))
                opening_rank = base_opening_rank + int(random.uniform(-50, 50))
                closing_rank = base_closing_rank + int(random.uniform(-100, 100))
                percentile = round(random.uniform(92.0, 96.0), 2)
                eligible = base_eligible
                placed = int(eligible * random.uniform(0.85, 0.92))
                median_lpa = round(random.uniform(7.5, 9.0) + (i * 0.4), 2)
                max_lpa = round(median_lpa * random.uniform(2.5, 3.5), 2)

            elif scenario_type in ("ADMISSIONS_CRASH", "ADMISSIONS_DECLINE"):
                # Healthy initially, then sharp admissions collapse in year 3+
                intake = base_intake
                if i < 2:
                    enrolled = int(base_intake * 0.95)
                    opening_rank = base_opening_rank
                    closing_rank = base_closing_rank
                    percentile = 92.0
                else:
                    drop_factor = 0.55 - (i * 0.08)  # Drops to 35% enrolled (65% vacancy)
                    enrolled = int(base_intake * max(0.35, drop_factor))
                    opening_rank = int(base_opening_rank * (1.2 + (i * 0.15)))
                    closing_rank = int(base_closing_rank * (1.3 + (i * 0.15)))
                    percentile = max(55.0, 92.0 - (i * 8.0))
                eligible = max(60, int(enrolled * 0.95))
                placed = int(eligible * 0.78)
                median_lpa = 7.0
                max_lpa = 18.0

            elif scenario_type in ("PLACEMENT_COLLAPSE", "PLACEMENT_DETERIORATION"):
                intake = base_intake
                enrolled = int(base_intake * 0.92)
                opening_rank = base_opening_rank
                closing_rank = base_closing_rank
                percentile = 91.0
                eligible = base_eligible
                if i < 2:
                    placed = int(eligible * 0.88)
                    median_lpa = 8.0
                else:
                    placed = int(eligible * (0.40 - ((i - 2) * 0.10)))  # Collapses to 20%
                    median_lpa = max(3.0, 8.0 - ((i - 2) * 2.0))
                max_lpa = median_lpa * 2.0

            elif scenario_type == "RANKING_DETERIORATION":
                # Admissions and placements hold moderately, but CET cutoff rank deteriorates sharply
                intake = base_intake
                enrolled = int(base_intake * 0.84)
                eligible = int(enrolled * 0.95)
                placed = int(eligible * 0.72)
                median_lpa = 6.5
                max_lpa = 15.0
                if i < 2:
                    opening_rank = base_opening_rank + (i * 100)
                    closing_rank = base_closing_rank + (i * 200)
                    percentile = 91.5 - i
                else:
                    opening_rank = int(base_opening_rank * (1.5 + (i * 0.6)))
                    closing_rank = int(base_closing_rank * (1.8 + (i * 0.9)))  # Surges to >22,000
                    percentile = max(42.0, 90.0 - (i * 11.5))

            elif scenario_type == "CROSS_SIGNAL_CRISIS":
                # Ranking improves (cutoff rank gets lower/better) and admissions grow to near 100%,
                # while placements collapse precipitously (classic decoupled cross-signal trap)
                intake = base_intake
                enrolled = min(base_intake, int(base_intake * (0.90 + (i * 0.02))))  # Rises from 270 to 294
                opening_rank = max(800, base_opening_rank - (i * 120))              # Improves
                closing_rank = max(2800, base_closing_rank - (i * 400))             # Improves from 5000 -> 3400
                percentile = min(98.5, 92.0 + (i * 1.2))
                eligible = int(enrolled * 0.96)
                if i < 2:
                    placed = int(eligible * 0.88)
                    median_lpa = 8.5
                else:
                    placed = int(eligible * max(0.18, 0.42 - ((i - 2) * 0.12)))    # Collapses to 18%
                    median_lpa = max(3.2, 8.0 - ((i - 1) * 1.5))
                max_lpa = median_lpa * 2.0

            elif scenario_type == "CASCADING_CRISIS":
                # Compounding multi-signal degradation
                intake = base_intake
                enrolled = int(base_intake * max(0.25, 0.95 - (i * 0.16)))
                opening_rank = int(base_opening_rank * (1.0 + (i * 0.4)))
                closing_rank = int(base_closing_rank * (1.0 + (i * 0.6)))
                percentile = max(35.0, 93.0 - (i * 14.0))
                eligible = max(50, int(base_eligible - (i * 45)))
                placed = int(eligible * max(0.20, 0.85 - (i * 0.18)))
                median_lpa = max(3.0, 8.5 - (i * 1.2))
                max_lpa = median_lpa * 2.2

            elif scenario_type == "GRADUAL_DECLINE":
                # Slow, steady multi-period erosion across all three signals
                intake = base_intake
                enrolled = int(base_intake * (0.94 - (i * 0.07)))        # 94% -> 66% (vacancy 34%)
                opening_rank = int(base_opening_rank * (1.0 + (i * 0.18)))
                closing_rank = int(base_closing_rank * (1.0 + (i * 0.25)))  # 5000 -> 10000
                percentile = round(92.0 - (i * 5.5), 2)
                eligible = int(enrolled * 0.95)
                placed = int(eligible * (0.84 - (i * 0.09)))             # 84% -> 48%
                median_lpa = round(8.0 - (i * 0.7), 2)
                max_lpa = round(median_lpa * 2.2, 2)

            elif scenario_type == "SUDDEN_CRISIS":
                # 4 years completely stable, then acute sudden shock in final year
                intake = base_intake
                if i < years_count - 1:
                    enrolled = int(base_intake * (0.96 + (0.01 if i % 2 == 0 else -0.01)))
                    opening_rank = base_opening_rank + (25 if i % 2 == 0 else -25)
                    closing_rank = base_closing_rank + (50 if i % 2 == 0 else -50)
                    percentile = 94.0
                    eligible = base_eligible
                    placed = int(eligible * (0.88 + (0.01 if i % 2 == 0 else -0.01)))
                    median_lpa = 8.2
                    max_lpa = 21.0
                else:
                    enrolled = int(base_intake * 0.38)  # Sudden 62% vacancy shock
                    opening_rank = int(base_opening_rank * 2.5)
                    closing_rank = int(base_closing_rank * 3.2)  # Sudden spike to 16,000
                    percentile = 48.0
                    eligible = int(enrolled * 0.90)
                    placed = int(eligible * 0.24)  # Sudden drop to 24% placed
                    median_lpa = 3.5
                    max_lpa = 8.0

            elif scenario_type == "RECOVERY":
                # Crisis in early/mid years (i=0,1,2) followed by strong institutional recovery in years 3,4
                intake = base_intake
                if i <= 1:
                    enrolled = int(base_intake * (0.52 + (i * 0.04)))
                    opening_rank = int(base_opening_rank * (2.0 - (i * 0.1)))
                    closing_rank = int(base_closing_rank * (2.2 - (i * 0.15)))  # ~11,000 -> 10,250
                    percentile = 58.0 + (i * 4.0)
                    eligible = int(enrolled * 0.92)
                    placed = int(eligible * (0.42 + (i * 0.06)))
                    median_lpa = 4.2 + (i * 0.5)
                else:
                    enrolled = int(base_intake * min(0.96, 0.78 + ((i - 2) * 0.08)))  # Recovers to 94%
                    opening_rank = int(base_opening_rank * max(1.0, 1.4 - ((i - 2) * 0.2)))
                    closing_rank = int(base_closing_rank * max(1.02, 1.5 - ((i - 2) * 0.24)))  # Recovers to 5,100
                    percentile = min(94.0, 76.0 + ((i - 2) * 8.5))
                    eligible = int(enrolled * 0.95)
                    placed = int(eligible * min(0.88, 0.68 + ((i - 2) * 0.10)))  # Recovers to 88%
                    median_lpa = round(6.0 + ((i - 2) * 1.1), 2)
                max_lpa = round(median_lpa * 2.4, 2)

            elif scenario_type == "NOISY_MISSING_DATA":
                # Sparse observations with moderate jitter
                intake = base_intake
                enrolled = int(base_intake * (0.86 if i == 0 else 0.81))
                opening_rank = base_opening_rank + (120 if i == 0 else 450)
                closing_rank = base_closing_rank + (300 if i == 0 else 950)
                percentile = 89.0 if i == 0 else 84.5
                eligible = int(enrolled * 0.94)
                placed = int(eligible * (0.79 if i == 0 else 0.73))
                median_lpa = 7.2 if i == 0 else 6.8
                max_lpa = 16.5

            else:
                raise ValueError(f"Unknown scenario_type: {scenario_type}")

            vacancy = intake - enrolled
            vacancy_rate = round(float(vacancy) / float(intake), 4)
            unplaced = eligible - placed
            placement_pct = round((float(placed) / float(eligible)) * 100.0, 2)

            cet_records.append({
                "institution_id": institution_id,
                "academic_year": year,
                "department": "Engineering & Technology",
                "quota_category": "General",
                "opening_rank": opening_rank,
                "closing_rank": closing_rank,
                "percentile_cutoff": percentile
            })

            admissions_records.append({
                "institution_id": institution_id,
                "academic_year": year,
                "department": "Engineering & Technology",
                "sanctioned_intake": intake,
                "enrolled_count": enrolled,
                "vacancy_count": vacancy,
                "vacancy_rate": vacancy_rate,
                "gender_diversity_ratio": 0.35,
                "dropouts_year_1": int(enrolled * 0.03)
            })

            placements_records.append({
                "institution_id": institution_id,
                "academic_year": year,
                "graduation_year": year,
                "department": "Engineering & Technology",
                "eligible_students": eligible,
                "placed_students": placed,
                "placement_percentage": placement_pct,
                "median_salary_lpa": median_lpa,
                "max_salary_lpa": max_lpa,
                "top_tier_recruiters_count": max(1, 10 - (i * 2)),
                "unplaced_count": unplaced
            })

        return {
            "cet_ranking": cet_records,
            "admissions": admissions_records,
            "placements": placements_records
        }
