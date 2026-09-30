import os
import pytest
from src.adapters.excel_adapter import ExcelInstitutionalAdapter
from src.engine import CrisisIntelligenceEngine

EXCEL_FILE = "C:/Users/Dell/OneDrive/Documents/antigravity/project data set.xlsx"

@pytest.mark.skipif(not os.path.exists(EXCEL_FILE), reason="Real excel file not found")
def test_excel_institutional_adapter_real_dataset():
    adapter = ExcelInstitutionalAdapter(EXCEL_FILE)
    parsed = adapter.parse()

    assert len(parsed["admissions"]) == 32
    assert len(parsed["placements"]) == 32
    assert len(parsed["cet_ranking"]) == 32

    sample_adm = parsed["admissions"][0]
    assert sample_adm.provenance is not None
    assert sample_adm.provenance.source_type == "institutional_export"
    assert sample_adm.institution_id == "RYMEC"

    engine = CrisisIntelligenceEngine()
    assessment = engine.evaluate_institution(
        "RYMEC",
        parsed["cet_ranking"],
        parsed["admissions"],
        parsed["placements"]
    )
    assert assessment.institution_id == "RYMEC"
    assert assessment.risk_level in ["HIGH", "CRITICAL"]
    assert assessment.composite_risk_index >= 0.50
