import pytest
from src.engine import SyntheticDataGenerator, TemporalAnomalyDetector, CrisisIntelligenceEngine
from src.adapters import JSONDictionaryAdapter

def test_synthetic_generator_healthy_scenario():
    raw_data = SyntheticDataGenerator.generate_scenario("INST_HEALTHY", "HEALTHY", 2020, 5)
    adapter = JSONDictionaryAdapter()
    parsed = adapter.parse(raw_data)
    
    assert len(parsed["cet_ranking"]) == 5
    assert len(parsed["admissions"]) == 5
    assert len(parsed["placements"]) == 5

    engine = CrisisIntelligenceEngine()
    assessment = engine.evaluate_institution(
        "INST_HEALTHY",
        parsed["cet_ranking"],
        parsed["admissions"],
        parsed["placements"]
    )
    assert assessment.risk_level in ["LOW", "MEDIUM"]
    assert assessment.composite_risk_index < 0.45

def test_synthetic_generator_cascading_crisis():
    raw_data = SyntheticDataGenerator.generate_scenario("INST_CRISIS", "CASCADING_CRISIS", 2020, 5)
    adapter = JSONDictionaryAdapter()
    parsed = adapter.parse(raw_data)
    
    engine = CrisisIntelligenceEngine()
    assessment = engine.evaluate_institution(
        "INST_CRISIS",
        parsed["cet_ranking"],
        parsed["admissions"],
        parsed["placements"]
    )
    assert assessment.risk_level in ["CRITICAL", "HIGH"]
    assert assessment.composite_risk_index >= 0.50
    assert len(assessment.anomalies_detected) > 0
    assert len(assessment.recommended_mitigations) > 0

def test_anomaly_detection_pure_math():
    detector = TemporalAnomalyDetector()
    z = detector.compute_zscore([100.0, 100.0, 100.0, 100.0], 50.0)
    # When baseline variance is zero, zscore returns 0.0
    assert z == 0.0

    z_varying = detector.compute_zscore([100.0, 95.0, 105.0, 100.0], 50.0)
    assert z_varying < -5.0 # Extreme negative outlier
