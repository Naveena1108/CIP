"""
Comprehensive Verification Suite for CIP Phase 3: Institutional Intelligence.

Tests all required Phase 3 capabilities:
1. Institution-specific historical baselines (ESTABLISHED vs TWO_PERIOD_COMPARISON_ONLY vs INSUFFICIENT_HISTORY)
2. Multi-dimensional anomaly evaluation (magnitude, historical deviation, persistence, coverage, materiality, cross-signal confirmation) & 6-part explanation
3. 5-stage risk progression (Observation -> Anomaly -> Emerging Risk -> Institutional Risk -> Crisis; never labeling every anomaly a crisis)
4. Cross-signal reasoning (e.g., placement decline -> internships, industry, academics/career services, feedback, faculty) & correlation vs causation ('potential contributing factor')
5. InvestigationObject structure & missing evidence representation
6. Source contradictions surfaced in intelligence, baselines, investigations, and epistemic unknowns
7. Explicit epistemic separation: OBSERVED_FACT -> ANALYSIS -> INFERENCE -> PREDICTION -> RECOMMENDATION + UNKNOWN_INSUFFICIENT_EVIDENCE
8. Early warning progression: weak_signal -> repeated_anomaly -> cross_signal_confirmation -> emerging_risk
9. What-changed state comparison: new_signals, worsening_signals, improving_signals, resolved_risks, new_risks, changed_relationships, changed_forecasts
10. End-to-end FastAPI routes and RYMEC validation dataset compatibility
"""

import os
from typing import AsyncGenerator
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.api.main import app
from src.contracts import (
    DiscoveredSignal,
    InstitutionalContextAssociation,
    SignalProvenance,
)
from src.db.models import Base
from src.db.session import get_db_session
from src.engine.institutional_intelligence import InstitutionalIntelligenceEngine


TEST_PHASE3_DB_URL = "sqlite+aiosqlite:///./test_phase3_intelligence.db"


@pytest_asyncio.fixture(scope="module")
async def phase3_client() -> AsyncGenerator[AsyncClient, None]:
    engine = create_async_engine(TEST_PHASE3_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db_session] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        yield client

    app.dependency_overrides.clear()
    await engine.dispose()
    if os.path.exists("./test_phase3_intelligence.db"):
        try:
            os.remove("./test_phase3_intelligence.db")
        except OSError:
            pass


def _make_signal(
    domain: str,
    metric_name: str,
    metric_label: str,
    value: float,
    unit: str,
    polarity: str,
    dept: str | None,
    year: int | None,
    source: str = "institutional_audit.csv",
    risk_contribution: float | None = None,
    is_contradictory: bool = False,
    contradiction_group_id: str | None = None,
) -> DiscoveredSignal:
    return DiscoveredSignal(
        signal_id=f"sig_{domain}_{metric_name}_{dept or 'INST'}_{year or 'NA'}_{source}_{value}",
        domain=domain,
        metric_name=metric_name,
        metric_label=metric_label,
        value=value,
        raw_value=f"{value}{unit if unit == '%' else ''}",
        unit=unit,
        polarity=polarity,
        risk_contribution=risk_contribution,
        is_contradictory=is_contradictory,
        contradiction_group_id=contradiction_group_id,
        context=InstitutionalContextAssociation(
            institution_id="INST_TEST",
            department=dept,
            time_period=str(year) if year is not None else None,
            academic_year=year,
            source=source,
        ),
        provenance=SignalProvenance(
            source=source,
            document=source,
            format_type="CSV",
            page_or_section=f"AY {year}" if year else "Unattributed",
            spreadsheet_location=f"Sheet1!R2C2",
            excerpt_or_reference=f"{metric_label} ({dept or 'INST'}, {year}): {value}",
            extraction_confidence=0.95,
        ),
    )


def test_phase3_baselines_established_vs_insufficient_history():
    """
    Verify institution-specific baselines:
    - >= 3 periods -> ESTABLISHED with statistical mean, std, median, min, max, slope
    - 2 periods -> TWO_PERIOD_COMPARISON_ONLY with prior_value populated and baseline_mean=None (not fabricated)
    - 1 period (or 0 dated periods) -> INSUFFICIENT_HISTORY with baseline_mean=None (never fabricated)
    """
    engine = InstitutionalIntelligenceEngine()
    signals = [
        # 4 periods for placements in CSE -> ESTABLISHED baseline (using 2021, 2022, 2023)
        _make_signal("placements", "placement_percentage", "Placement Percentage", 84.0, "%", "higher_is_better", "CSE", 2021),
        _make_signal("placements", "placement_percentage", "Placement Percentage", 82.0, "%", "higher_is_better", "CSE", 2022),
        _make_signal("placements", "placement_percentage", "Placement Percentage", 80.0, "%", "higher_is_better", "CSE", 2023),
        _make_signal("placements", "placement_percentage", "Placement Percentage", 52.0, "%", "higher_is_better", "CSE", 2024),
        # 2 periods for internships in CSE -> TWO_PERIOD_COMPARISON_ONLY (do not fabricate statistical baseline_mean)
        _make_signal("internships", "internship_percentage", "Internship Percentage", 75.0, "%", "higher_is_better", "CSE", 2023),
        _make_signal("internships", "internship_percentage", "Internship Percentage", 55.0, "%", "higher_is_better", "CSE", 2024),
        # 1 period for library volumes -> INSUFFICIENT_HISTORY (do not fabricate baseline)
        _make_signal("infrastructure", "library_volumes", "Library Volumes", 45000.0, "count", "higher_is_better", None, 2024),
    ]

    baselines = engine.build_baselines("INST_TEST", signals)
    b_map = {(b.domain, b.metric_name): b for b in baselines}

    plc_b = b_map[("placements", "placement_percentage")]
    assert plc_b.baseline_status == "ESTABLISHED"
    assert plc_b.historical_periods_used == [2021, 2022, 2023]
    assert plc_b.baseline_mean == 82.0
    assert plc_b.baseline_std == 2.0
    assert plc_b.baseline_median == 82.0
    assert plc_b.latest_period == 2024
    assert plc_b.latest_value == 52.0

    int_b = b_map[("internships", "internship_percentage")]
    assert int_b.baseline_status == "TWO_PERIOD_COMPARISON_ONLY"
    assert int_b.baseline_mean is None
    assert int_b.baseline_std is None
    assert int_b.prior_period == 2023
    assert int_b.prior_value == 75.0
    assert int_b.latest_period == 2024
    assert int_b.latest_value == 55.0

    lib_b = b_map[("infrastructure", "library_volumes")]
    assert lib_b.baseline_status == "INSUFFICIENT_HISTORY"
    assert lib_b.baseline_mean is None
    assert lib_b.baseline_std is None
    assert "never fabricates baselines" in lib_b.explanation


def test_phase3_anomalies_dimensions_and_six_part_explanation():
    """
    Verify that EvaluatedAnomaly computes magnitude, historical_deviation, persistence,
    coverage, materiality, and cross_signal_confirmation, and provides all 6 required
    explanation fields: what_changed, by_how_much, when, comparison_basis, significance, evidence.
    """
    engine = InstitutionalIntelligenceEngine()
    signals = [
        _make_signal("placements", "placement_percentage", "Placement Percentage", 85.0, "%", "higher_is_better", "CSE", 2021),
        _make_signal("placements", "placement_percentage", "Placement Percentage", 83.0, "%", "higher_is_better", "CSE", 2022),
        _make_signal("placements", "placement_percentage", "Placement Percentage", 68.0, "%", "higher_is_better", "CSE", 2023),
        _make_signal("placements", "placement_percentage", "Placement Percentage", 45.0, "%", "higher_is_better", "CSE", 2024),
        # Related signal in internships also declining -> cross-signal confirmation
        _make_signal("internships", "internship_percentage", "Internship Percentage", 78.0, "%", "higher_is_better", "CSE", 2022),
        _make_signal("internships", "internship_percentage", "Internship Percentage", 72.0, "%", "higher_is_better", "CSE", 2023),
        _make_signal("internships", "internship_percentage", "Internship Percentage", 48.0, "%", "higher_is_better", "CSE", 2024),
        # Another department (ECE) stays healthy so coverage is 1/2 = 0.50
        _make_signal("placements", "placement_percentage", "Placement Percentage", 80.0, "%", "higher_is_better", "ECE", 2022),
        _make_signal("placements", "placement_percentage", "Placement Percentage", 81.0, "%", "higher_is_better", "ECE", 2023),
        _make_signal("placements", "placement_percentage", "Placement Percentage", 82.0, "%", "higher_is_better", "ECE", 2024),
    ]

    baselines = engine.build_baselines("INST_TEST", signals)
    anomalies = engine.evaluate_anomalies("INST_TEST", signals, baselines)
    plc_anom = next(a for a in anomalies if a.domain == "placements" and a.department == "CSE")

    # Verify all 6 dimensions
    assert plc_anom.magnitude_score > 0.70
    assert plc_anom.historical_deviation_zscore is not None and plc_anom.historical_deviation_zscore < -2.0
    assert plc_anom.persistence_periods == 3  # 2021->2022->2023->2024 consecutive drops
    assert plc_anom.coverage_ratio == 0.50
    assert plc_anom.materiality_score >= 0.80
    assert plc_anom.cross_signal_confirmation is True
    assert any("internships.internship_percentage" in c for c in plc_anom.confirming_signals)

    # Verify all 6 required explanation fields
    assert "Placement Percentage" in plc_anom.what_changed and "CSE" in plc_anom.what_changed
    assert "45.00%" in plc_anom.by_how_much and "Z-score" in plc_anom.by_how_much
    assert "AY 2024" in plc_anom.when and "persistent" in plc_anom.when
    assert "Institution-specific historical baseline" in plc_anom.comparison_basis
    assert "composite significance=" in plc_anom.significance
    assert len(plc_anom.evidence) >= 1


def test_phase3_risk_progression_ladder_does_not_label_every_anomaly_a_crisis():
    """
    Verify the 5-stage risk progression ladder:
    Observation -> Anomaly -> Emerging Risk -> Institutional Risk -> Crisis.
    Ensure an isolated 1-period anomaly in a single department is classified as 'Anomaly', NOT 'Crisis'.
    """
    engine = InstitutionalIntelligenceEngine()

    # Scenario A: Single-period localized drop in 1 out of 3 departments, with no cross-signal confirmation
    localized_signals = [
        # CSE has a 1-year drop in 2024 after stable/improving 2021-2023
        _make_signal("academics", "pass_percentage", "Pass Percentage", 88.0, "%", "higher_is_better", "CSE", 2021),
        _make_signal("academics", "pass_percentage", "Pass Percentage", 89.0, "%", "higher_is_better", "CSE", 2022),
        _make_signal("academics", "pass_percentage", "Pass Percentage", 90.0, "%", "higher_is_better", "CSE", 2023),
        _make_signal("academics", "pass_percentage", "Pass Percentage", 74.0, "%", "higher_is_better", "CSE", 2024),
        # ECE and MECH are completely healthy
        _make_signal("academics", "pass_percentage", "Pass Percentage", 86.0, "%", "higher_is_better", "ECE", 2022),
        _make_signal("academics", "pass_percentage", "Pass Percentage", 87.0, "%", "higher_is_better", "ECE", 2023),
        _make_signal("academics", "pass_percentage", "Pass Percentage", 88.0, "%", "higher_is_better", "ECE", 2024),
        _make_signal("academics", "pass_percentage", "Pass Percentage", 84.0, "%", "higher_is_better", "MECH", 2022),
        _make_signal("academics", "pass_percentage", "Pass Percentage", 85.0, "%", "higher_is_better", "MECH", 2023),
        _make_signal("academics", "pass_percentage", "Pass Percentage", 86.0, "%", "higher_is_better", "MECH", 2024),
    ]
    report_local = engine.analyze_institution("INST_LOCAL", dynamic_signals=localized_signals)
    assert len(report_local.risk_progression.anomalies) == 1
    assert len(report_local.risk_progression.crises) == 0
    assert report_local.risk_progression.overall_institutional_stage == "Anomaly"
    assert report_local.risk_progression.observations  # ECE and MECH remain Observations

    # Scenario B: Multi-period + cross-signal confirmed institution-wide collapse -> escalates to Institutional Risk / Crisis
    severe_signals = [
        _make_signal("placements", "placement_percentage", "Placement Percentage", 85.0, "%", "higher_is_better", None, 2021),
        _make_signal("placements", "placement_percentage", "Placement Percentage", 72.0, "%", "higher_is_better", None, 2022),
        _make_signal("placements", "placement_percentage", "Placement Percentage", 55.0, "%", "higher_is_better", None, 2023),
        _make_signal("placements", "placement_percentage", "Placement Percentage", 28.0, "%", "higher_is_better", None, 2024, risk_contribution=0.85),
        _make_signal("internships", "internship_percentage", "Internship Percentage", 80.0, "%", "higher_is_better", None, 2021),
        _make_signal("internships", "internship_percentage", "Internship Percentage", 68.0, "%", "higher_is_better", None, 2022),
        _make_signal("internships", "internship_percentage", "Internship Percentage", 49.0, "%", "higher_is_better", None, 2023),
        _make_signal("internships", "internship_percentage", "Internship Percentage", 25.0, "%", "higher_is_better", None, 2024, risk_contribution=0.82),
    ]
    report_severe = engine.analyze_institution("INST_SEVERE", dynamic_signals=severe_signals)
    assert report_severe.risk_progression.overall_institutional_stage in ("Institutional Risk", "Crisis")
    assert len(report_severe.risk_progression.crises) + len(report_severe.risk_progression.institutional_risks) >= 1


def test_phase3_cross_signal_reasoning_and_investigation_objects():
    """
    Verify that when placements decline, the engine inspects related signals across
    internships, industry, academics, feedback, and faculty; distinguishes correlation
    from causation using 'Potential contributing factor'; and builds complete InvestigationObjects.
    """
    engine = InstitutionalIntelligenceEngine()
    signals = [
        # Primary: Placement decline in CSE
        _make_signal("placements", "placement_percentage", "Placement Percentage", 86.0, "%", "higher_is_better", "CSE", 2022),
        _make_signal("placements", "placement_percentage", "Placement Percentage", 71.0, "%", "higher_is_better", "CSE", 2023),
        _make_signal("placements", "placement_percentage", "Placement Percentage", 44.0, "%", "higher_is_better", "CSE", 2024),
        # Related 1: Internships declined (CORROBORATING_CHANGE)
        _make_signal("internships", "internship_percentage", "Internship Percentage", 78.0, "%", "higher_is_better", "CSE", 2023),
        _make_signal("internships", "internship_percentage", "Internship Percentage", 49.0, "%", "higher_is_better", "CSE", 2024),
        # Related 2: Industry MoUs declined (CORROBORATING_CHANGE)
        _make_signal("industry", "active_mous", "Active Industry MoUs", 14.0, "count", "higher_is_better", "CSE", 2023),
        _make_signal("industry", "active_mous", "Active Industry MoUs", 6.0, "count", "higher_is_better", "CSE", 2024),
        # Related 3: Faculty attrition worsened (CORROBORATING_CHANGE)
        _make_signal("faculty", "faculty_attrition_rate", "Faculty Attrition Rate", 8.0, "%", "lower_is_better", "CSE", 2023),
        _make_signal("faculty", "faculty_attrition_rate", "Faculty Attrition Rate", 22.0, "%", "lower_is_better", "CSE", 2024),
        # Related 4: Academics pass percentage stayed stable (STABLE_NO_CHANGE)
        _make_signal("academics", "pass_percentage", "Pass Percentage", 88.0, "%", "higher_is_better", "CSE", 2023),
        _make_signal("academics", "pass_percentage", "Pass Percentage", 88.5, "%", "higher_is_better", "CSE", 2024),
        # Note: 'feedback' and 'admissions' are intentionally omitted to verify missing_information detection!
    ]

    report = engine.analyze_institution("INST_CROSS", dynamic_signals=signals)

    plc_cs = next(c for c in report.cross_signal_findings if c.primary_domain == "placements")
    inspected_domains = {r.domain for r in plc_cs.inspected_related_signals}
    assert {"internships", "industry", "faculty", "academics"}.issubset(inspected_domains)
    assert set(plc_cs.corroborating_domains).issuperset({"internships", "industry", "faculty"})
    assert "feedback" in plc_cs.uninspected_missing_domains
    assert plc_cs.causal_evidence_present is False
    assert "correlation" in plc_cs.correlation_vs_causation_note.lower()
    assert all(pf.startswith("Potential contributing factor:") for pf in plc_cs.potential_contributing_factors)

    # Check InvestigationObject for placements
    plc_inv = next(i for i in report.investigations if i.domain == "placements")
    assert plc_inv.finding
    assert len(plc_inv.evidence) >= 1
    assert len(plc_inv.related_signals) >= 4
    assert any("internships" in pf for pf in plc_inv.potential_contributing_factors)
    assert len(plc_inv.alternative_explanations) >= 1
    assert 0.15 <= plc_inv.confidence <= 0.98
    assert any("feedback" in m for m in plc_inv.missing_information)


def test_phase3_contradictions_and_epistemic_separation():
    """
    Verify:
    1. Source contradictions are surfaced in contradictions_surfaced, baselines, investigations, and epistemic unknowns.
    2. EpistemicIntelligenceModel cleanly separates OBSERVED_FACT, ANALYSIS, INFERENCE, PREDICTION,
       RECOMMENDATION, and UNKNOWN_INSUFFICIENT_EVIDENCE.
    """
    engine = InstitutionalIntelligenceEngine()
    signals = [
        _make_signal("placements", "placement_percentage", "Placement Percentage", 82.0, "%", "higher_is_better", "CSE", 2022),
        _make_signal("placements", "placement_percentage", "Placement Percentage", 76.0, "%", "higher_is_better", "CSE", 2023),
        # Two conflicting sources for 2024 CSE placement_percentage!
        _make_signal(
            "placements", "placement_percentage", "Placement Percentage", 81.0, "%", "higher_is_better", "CSE", 2024,
            source="marketing_brochure.pdf", is_contradictory=True, contradiction_group_id="cg_plc_2024"
        ),
        _make_signal(
            "placements", "placement_percentage", "Placement Percentage", 46.0, "%", "higher_is_better", "CSE", 2024,
            source="auditor_verified_sheet.xlsx", is_contradictory=True, contradiction_group_id="cg_plc_2024"
        ),
        # Single-period metric -> triggers UNKNOWN_INSUFFICIENT_EVIDENCE for baseline
        _make_signal("grievances", "pending_grievances", "Pending Grievances", 19.0, "count", "lower_is_better", None, 2024, risk_contribution=0.55),
    ]

    report = engine.analyze_institution("INST_EPISTEMIC", dynamic_signals=signals)

    # Contradictions surfaced
    assert len(report.contradictions_surfaced) == 1
    contra = report.contradictions_surfaced[0]
    assert contra["domain"] == "placements"
    assert len(contra["conflicting_observations"]) == 2

    plc_base = next(b for b in report.baselines if b.domain == "placements")
    assert plc_base.has_contradictions is True

    # Epistemic separation verification
    em = report.epistemic_model
    assert len(em.observed_facts) >= 4
    assert all(s.epistemic_type == "OBSERVED_FACT" for s in em.observed_facts)
    assert len(em.analyses) >= 2
    assert all(s.epistemic_type == "ANALYSIS" for s in em.analyses)
    assert len(em.inferences) >= 1
    assert all(s.epistemic_type == "INFERENCE" for s in em.inferences)
    assert len(em.predictions) == 3  # Year +1, +2, +3
    assert all(s.epistemic_type == "PREDICTION" for s in em.predictions)
    assert len(em.recommendations) >= 1
    assert all(s.epistemic_type == "RECOMMENDATION" for s in em.recommendations)
    assert len(em.unknown_and_insufficient_evidence) >= 2
    assert all(s.epistemic_type == "UNKNOWN_INSUFFICIENT_EVIDENCE" for s in em.unknown_and_insufficient_evidence)
    assert any("contradiction" in u.statement.lower() for u in em.unknown_and_insufficient_evidence)
    assert any("insufficient historical data" in u.statement.lower() for u in em.unknown_and_insufficient_evidence)


def test_phase3_early_warning_and_what_changed_analysis():
    """
    Verify:
    1. Early Warning progression across weak_signal -> repeated_anomaly -> cross_signal_confirmation -> emerging_risk.
    2. What-Changed state comparison across new_signals, worsening_signals, improving_signals,
       resolved_risks, new_risks, changed_relationships, and changed_forecasts.
    """
    engine = InstitutionalIntelligenceEngine()
    signals = [
        # 1. Weak signal: Attendance dipped slightly by 6% in 2024 (below 10% anomaly threshold)
        _make_signal("attendance", "average_attendance", "Average Attendance", 86.0, "%", "higher_is_better", "CSE", 2022),
        _make_signal("attendance", "average_attendance", "Average Attendance", 86.0, "%", "higher_is_better", "CSE", 2023),
        _make_signal("attendance", "average_attendance", "Average Attendance", 80.5, "%", "higher_is_better", "CSE", 2024),

        # 2. Repeated anomaly (Stage 2, unconfirmed): Research publications in MECH dropped 2 consecutive years without related MECH signals
        _make_signal("research", "publications_count", "Research Publications", 50.0, "count", "higher_is_better", "MECH", 2022),
        _make_signal("research", "publications_count", "Research Publications", 42.0, "count", "higher_is_better", "MECH", 2023),
        _make_signal("research", "publications_count", "Research Publications", 32.0, "count", "higher_is_better", "MECH", 2024),

        # 3. Cross-signal confirmation (Stage 3, 1-period drop corroborated) & Emerging risk (Stage 4, 2-period drop corroborated):
        # Placements dropped in 2023 and 2024 (2 periods), corroborated by internship drop in 2024 (1 period after rising in 2023)
        _make_signal("placements", "placement_percentage", "Placement Percentage", 88.0, "%", "higher_is_better", "CSE", 2022),
        _make_signal("placements", "placement_percentage", "Placement Percentage", 72.0, "%", "higher_is_better", "CSE", 2023),
        _make_signal("placements", "placement_percentage", "Placement Percentage", 48.0, "%", "higher_is_better", "CSE", 2024, risk_contribution=0.52),
        _make_signal("internships", "internship_percentage", "Internship Percentage", 80.0, "%", "higher_is_better", "CSE", 2022),
        _make_signal("internships", "internship_percentage", "Internship Percentage", 81.0, "%", "higher_is_better", "CSE", 2023),
        _make_signal("internships", "internship_percentage", "Internship Percentage", 50.0, "%", "higher_is_better", "CSE", 2024, risk_contribution=0.50),

        # 4. Resolved risk & improving signal:
        # Finance salary delay was high in 2023 (risk_contribution=0.65) and resolved to 0 in 2024 (risk_contribution=0.0)
        _make_signal("finance", "salary_delay_months", "Salary Delay", 0.0, "months", "lower_is_better", None, 2022, risk_contribution=0.0),
        _make_signal("finance", "salary_delay_months", "Salary Delay", 3.0, "months", "lower_is_better", None, 2023, risk_contribution=0.65),
        _make_signal("finance", "salary_delay_months", "Salary Delay", 0.0, "months", "lower_is_better", None, 2024, risk_contribution=0.0),

        # 5. Brand new signal introduced only in 2024:
        _make_signal("accreditation", "naac_cgpa", "NAAC CGPA", 3.35, "score", "higher_is_better", None, 2024, risk_contribution=0.05),
    ]

    report = engine.analyze_institution("INST_EW_DELTA", dynamic_signals=signals)

    # Verify all 4 Early Warning stages
    ew_by_domain = {w.domain: w for w in report.early_warnings}
    assert "attendance" in ew_by_domain
    assert ew_by_domain["attendance"].current_stage == "weak_signal"
    assert ew_by_domain["attendance"].stage_index == 1

    assert "research" in ew_by_domain
    assert ew_by_domain["research"].current_stage == "repeated_anomaly"
    assert ew_by_domain["research"].stage_index == 2

    assert "internships" in ew_by_domain
    assert ew_by_domain["internships"].current_stage == "cross_signal_confirmation"
    assert ew_by_domain["internships"].stage_index == 3

    assert "placements" in ew_by_domain
    assert ew_by_domain["placements"].current_stage == "emerging_risk"
    assert ew_by_domain["placements"].stage_index == 4
    assert len(ew_by_domain["placements"].progression_chain) == 4

    # Verify What-Changed Report
    wc = report.what_changed
    assert wc.comparison_available is True
    assert any(s.domain == "accreditation" and s.direction == "NEW" for s in wc.new_signals)
    assert any(s.domain == "placements" and s.direction == "WORSENING" for s in wc.worsening_signals)
    assert any(s.domain == "finance" and s.direction == "IMPROVING" for s in wc.improving_signals)
    assert any(r.domain == "finance" and r.change_type == "RESOLVED_RISK" for r in wc.resolved_risks)
    assert any(r.domain in ("placements", "internships") and r.change_type in ("NEW_RISK", "ESCALATED_RISK") for r in wc.new_risks)
    assert len(wc.changed_relationships) >= 1
    assert len(wc.changed_forecasts) == 1


@pytest.mark.asyncio
async def test_phase3_api_endpoints_and_rymec_intelligence(phase3_client: AsyncClient):
    """
    Verify all Phase 3 FastAPI routes over HTTP on the real RYMEC validation dataset
    and confirm deterministic CRI=0.573 is preserved.
    """
    rymec_path = r"C:\Users\Dell\OneDrive\Documents\antigravity\project data set.xlsx"
    if not os.path.exists(rymec_path):
        pytest.skip("RYMEC validation dataset not present on disk")

    with open(rymec_path, "rb") as f:
        rymec_bytes = f.read()

    client = phase3_client
    signup_res = await client.post(
        "/api/v1/auth/signup",
        json={"email": "phase3_lead@rymec-ballari.edu", "password": "SafePassword123!"},
    )
    assert signup_res.status_code == 200
    headers = {"Authorization": f"Bearer {signup_res.json()['access_token']}"}

    up_res = await client.post(
        "/api/v1/ingest/upload",
        headers=headers,
        files={"file": ("project data set.xlsx", rymec_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert up_res.status_code == 200
    inst_id = up_res.json()["institution_id"]
    assert inst_id == "RYMEC"

    # 1. Verify existing deterministic evaluation is 100% preserved
    eval_res = await client.get(f"/api/v1/institutions/{inst_id}/evaluate", headers=headers)
    assert eval_res.status_code == 200
    assert eval_res.json()["composite_risk_index"] == 0.573

    # 2. Verify full /intelligence endpoint
    intel_res = await client.get(f"/api/v1/institutions/{inst_id}/intelligence", headers=headers)
    assert intel_res.status_code == 200
    intel = intel_res.json()
    assert intel["institution_id"] == "RYMEC"
    assert intel["risk_progression"]["composite_risk_index"] == 0.573
    assert len(intel["epistemic_model"]["observed_facts"]) > 0
    assert len(intel["epistemic_model"]["analyses"]) > 0
    assert len(intel["epistemic_model"]["predictions"]) == 3
    assert len(intel["epistemic_model"]["recommendations"]) > 0
    assert len(intel["baselines"]) >= 15
    assert any(b["baseline_status"] == "ESTABLISHED" for b in intel["baselines"])
    assert len(intel["evaluated_anomalies"]) >= 1
    assert len(intel["investigations"]) >= 1
    assert len(intel["early_warnings"]) >= 1
    assert intel["what_changed"]["comparison_available"] is True

    # 3. Verify granular Phase 3 sub-endpoints
    for sub_ep in ("baselines", "anomalies", "risk-progression", "cross-signals", "investigations", "early-warnings", "what-changed"):
        r = await client.get(f"/api/v1/institutions/{inst_id}/{sub_ep}", headers=headers)
        assert r.status_code == 200
