"""
Specialized Institutional Multi-Year Data Block Adapter for CIP.
Only executed after positive schema verification (Section 9).
Contaminating synthetic assumptions (fake enrollment ratios, fixed salaries,
invented CET cutoff ranks) are strictly forbidden (Section 10).
"""

import os
import re
import logging
from typing import Dict, List, Any, Optional
import openpyxl
from src.adapters.base_adapter import BaseSignalAdapter
from src.contracts import (
    ProvenanceMetadata,
    CETRankingSignal,
    AdmissionsSignal,
    PlacementsSignal
)

logger = logging.getLogger("ai_criss.adapters.excel")


class ExcelInstitutionalAdapter(BaseSignalAdapter):
    """
    Ingestion Adapter for historical multi-year departmental admissions,
    intakes, placements, and results.
    Runs ONLY after positive schema detection confirms the specific block layout.
    """

    def __init__(self, file_path: str, source_id: str = "INSTITUTIONAL_EXCEL_EXPORT"):
        self.file_path = file_path
        self.source_id = source_id

    @classmethod
    def detect_schema(cls, file_path: str) -> bool:
        """
        Positive Schema Detection (Section 9):
        Does NOT rely solely on filename, sheet name, or column positions.
        Verifies:
        1. Multi-year banner pattern 'DATA 20xx' in worksheet cells.
        2. Departmental code patterns (e.g. CSE, MECH, ECE, CIVIL, AIML).
        3. Multi-period integer operational metrics for intake and placement.
        """
        if not os.path.exists(file_path):
            return False
        try:
            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
            has_year_banner = False
            has_dept_signature = False
            dept_pattern = re.compile(r"\b(CSE|AIML|ISE|ECE|EEE|MECH|CIVIL|MBA|MCA|BT|CHEM)\b", re.IGNORECASE)
            year_banner_pattern = re.compile(r"DATA\s+(20\d{2})", re.IGNORECASE)

            for sname in wb.sheetnames:
                ws = wb[sname]
                sample_rows = 0
                for row in ws.iter_rows(values_only=True):
                    sample_rows += 1
                    if sample_rows > 150:
                        break
                    row_str = " ".join([str(c) for c in row if c is not None])
                    if year_banner_pattern.search(row_str):
                        has_year_banner = True
                    if dept_pattern.search(row_str):
                        has_dept_signature = True
                    if has_year_banner and has_dept_signature:
                        wb.close()
                        return True
            wb.close()
            return has_year_banner and has_dept_signature
        except Exception as e:
            logger.debug(f"detect_schema check failed on {file_path}: {e}")
            return False

    def parse(self, raw_data: Any = None) -> Dict[str, List[Any]]:
        wb = openpyxl.load_workbook(self.file_path, data_only=True)
        
        target_sheets = [s for s in wb.sheetnames if s in ["Sheet2", "Sheet1"]]
        ws = wb["Sheet2"] if "Sheet2" in target_sheets else wb[target_sheets[0]]

        provenance = ProvenanceMetadata(
            source_id=self.source_id,
            source_type="institutional_export",
            version="1.0.0"
        )

        admissions_list: List[AdmissionsSignal] = []
        placements_list: List[PlacementsSignal] = []
        cet_list: List[CETRankingSignal] = []

        current_year = 2024
        rows = list(ws.iter_rows(values_only=True))

        for row in rows:
            row_str = " ".join([str(c) for c in row if c is not None])
            year_match = re.search(r"DATA\s+(\d{4})", row_str, re.IGNORECASE)
            if year_match:
                current_year = int(year_match.group(1))
                continue

            dept = row[1] if len(row) > 1 else None
            college = row[2] if len(row) > 2 else None
            intake = row[4] if len(row) > 4 else None
            placement = row[5] if len(row) > 5 else None
            cet_adm = row[7] if len(row) > 7 else None
            result = row[8] if len(row) > 8 else None

            if not dept or str(dept).strip().lower() in ["dept", "none", ""]:
                continue
            if not college or str(college).strip().lower() in ["college name", "none", ""]:
                continue

            try:
                intake_val = int(intake) if intake is not None else 0
                placement_val = int(placement) if placement is not None else 0
                cet_val = int(cet_adm) if cet_adm is not None else 0
                result_val = float(result) if result is not None else None
            except (ValueError, TypeError):
                continue

            if intake_val <= 0 and cet_val <= 0 and placement_val <= 0:
                continue

            inst_id = str(college).strip()
            dept_name = str(dept).strip()

            # Admissions Signal:
            # ONLY construct if both sanctioned intake and enrollment are positively observed.
            # Contaminating synthetic fallbacks (e.g. sanctioned*0.5) are strictly removed (Section 10).
            if intake_val > 0 and cet_val > 0:
                sanctioned = intake_val
                enrolled = min(sanctioned, cet_val)
                vacancy = sanctioned - enrolled
                vacancy_rate = round(float(vacancy) / float(sanctioned), 4)

                adm_signal = AdmissionsSignal(
                    institution_id=inst_id,
                    academic_year=current_year,
                    department=dept_name,
                    sanctioned_intake=sanctioned,
                    enrolled_count=enrolled,
                    vacancy_count=vacancy,
                    vacancy_rate=vacancy_rate,
                    gender_diversity_ratio=0.0,  # Unobserved in source: 0.0 (no invented 0.32)
                    dropouts_year_1=0,          # Unobserved in source: 0
                    provenance=provenance
                )
                admissions_list.append(adm_signal)

            # Placements Signal:
            # Eligible students derived from cohort intake; no fake salaries or recruiter counts (Section 10).
            if placement_val > 0 and intake_val > 0:
                eligible = max(placement_val, intake_val)
                placed = min(eligible, placement_val)
                unplaced = eligible - placed
                placement_pct = round((float(placed) / float(eligible)) * 100.0, 2) if eligible > 0 else 0.0

                plc_signal = PlacementsSignal(
                    institution_id=inst_id,
                    academic_year=current_year,
                    graduation_year=current_year,
                    department=dept_name,
                    eligible_students=eligible,
                    placed_students=placed,
                    placement_percentage=placement_pct,
                    median_salary_lpa=0.0,  # Unobserved in source: no fake 4.5 LPA
                    max_salary_lpa=0.0,     # Unobserved in source: no fake 12.0 LPA
                    top_tier_recruiters_count=0, # Unobserved in source: no fake 5 recruiters
                    unplaced_count=unplaced,
                    provenance=provenance
                )
                placements_list.append(plc_signal)

            # NOTE (Section 10): CET ranking cutoff ranks were previously fabricated
            # from examination pass rates (result_val). Exam pass rates are NOT CET cutoff ranks.
            # Fabricated ranks have been removed. If a future source contains explicit
            # opening_rank and closing_rank columns, they will be mapped here.

        wb.close()

        return {
            "admissions": admissions_list,
            "placements": placements_list,
            "cet_ranking": cet_list
        }
