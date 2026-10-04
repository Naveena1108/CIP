"""
Evaluation and Simulation Route Handlers for AI CRISS API.
Exposes crisis scoring, evidence dossier assembly, and autoregressive forecasting.
"""

import json
from typing import Any, Dict, List, Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_db_session
from src.db.repository import (
    AccessPolicyRepository,
    InstitutionRepository,
    OrganizationRepository,
    SignalSnapshotRepository,
    DiscoveredSignalRepository,
    IngestionRecordRepository,
    AssessmentRepository,
    InstitutionalMemoryRepository,
)
from src.contracts import (
    CrisisAssessment,
    AdmissionsSignal,
    PlacementsSignal,
    CETRankingSignal,
    DiscoveredSignal,
    DataQualityIssue,
    HistoricalBaseline,
    EvaluatedAnomaly,
    RiskProgressionLadder,
    CrossSignalFinding,
    InvestigationObject,
    EarlyWarningIndicator,
    WhatChangedReport,
    InstitutionalIntelligenceReport,
    ForecastStatus,
    MemoryCategory,
    ExplainableForecast,
    ForecastChangeExplanation,
    InstitutionalMemoryEntry,
    PredictionOutcomeComparisonRequest,
    PredictionOutcomeComparison,
    UserFeedbackSubmission,
    UserFeedbackRecord,
    InstitutionalMemoryStoreView,
    ProvenanceRecord,
    GroundedClaim,
    InsightWithEvidence,
    EvidenceProvenanceCatalog,
    NaturalQuestionRequest,
    NaturalQuestionInvestigationResponse,
    GeminiStatusResponse,
    OrganizationNetworkIntelligenceReport,
)
from src.engine.crisis_scorer import CrisisIntelligenceEngine, RiskWeightProfile
from src.engine.institutional_intelligence import InstitutionalIntelligenceEngine
from src.engine.explainable_forecasting import ExplainableForecastingAndMemoryEngine
from src.engine.investigation_engine import EvidenceGroundedInvestigationEngine
from src.engine.organization_intelligence import OrganizationNetworkIntelligenceEngine
from src.engine.features import extract_institutional_features, extract_department_features
from src.engine.evidence import EvidenceAssembler, InstitutionalDossier
from src.engine.predictor import TrajectoryPredictor, TrajectoryPoint
from src.engine.llm_reasoner import LLMStructuredReasoner, ExecutiveNarrativeResponse
from src.reporting.pdf_generator import generate_crisis_pdf
from src.api.auth import get_current_user
from src.db.models import UserModel
from src.services.analysis_persistence import AnalysisPersistenceService, OverviewResponse

router = APIRouter(prefix="/institutions", tags=["Institutions & Crisis Evaluation"])
org_router = APIRouter(prefix="/organizations", tags=["Organization & Network Intelligence"])


class InstitutionOut(BaseModel):
    id: str
    name: str
    state: str
    city: Optional[str] = None
    accreditation_grade: str
    entity_category: str = "educational_institution"
    entity_category_other: Optional[str] = None
    entity_type: str = "institution"
    entity_type_other: Optional[str] = None
    ownership_governance: Optional[str] = None
    ownership_governance_other: Optional[str] = None
    education_level: Optional[str] = None
    education_entity_type: Optional[str] = None
    education_entity_type_other: Optional[str] = None
    university_type: Optional[str] = None
    university_type_other: Optional[str] = None
    academic_domain: Optional[str] = None
    academic_domains: List[str] = Field(default_factory=list)
    academic_domain_other: Optional[str] = None
    parent_organization_id: Optional[str] = None
    organization_id: Optional[str] = None
    owner_user_id: Optional[str] = None


class RiskTrendPoint(BaseModel):
    academic_year: int
    period_label: str
    composite_risk_index: float
    risk_level: str
    primary_threat: str
    admissions_vacancy_rate: Optional[float] = None
    placement_percentage: Optional[float] = None
    anomalies_count: int = 0


class RiskTrendResponse(BaseModel):
    institution_id: str
    status: Literal["SUCCESS", "INSUFFICIENT_DATA", "FAILED"]
    message: str
    distinct_periods: int
    trend_direction: Literal["WORSENING", "IMPROVING", "STABLE", "INSUFFICIENT_DATA"]
    historical_points: List[RiskTrendPoint] = Field(default_factory=list)
    projected_trajectory: List[TrajectoryPoint] = Field(default_factory=list)
    human_summary: str


class PredictionResponse(BaseModel):
    institution_id: str
    status: Literal["SUCCESS", "INSUFFICIENT_EVIDENCE", "FAILED"]
    headline: str
    summary: str
    why_it_matters: str
    what_to_check: str
    method_used: str
    confidence: float
    horizon_years: int
    current_cri: float
    projections: List[TrajectoryPoint] = Field(default_factory=list)
    historical_periods_used: List[int] = Field(default_factory=list)



async def _resolve_org_id(
    session: AsyncSession,
    institution_id: str,
    current_user: Optional[UserModel] = None,
) -> Optional[str]:
    inst = await InstitutionRepository.get_by_id(session, institution_id)
    if inst and (inst.organization_id or inst.parent_organization_id):
        return inst.organization_id or inst.parent_organization_id
    if current_user and current_user.organization_id:
        return current_user.organization_id
    return None


def _apply_department_scope_filter(
    institution_id: str,
    current_user: Optional[UserModel],
    cet: List[CETRankingSignal],
    admissions: List[AdmissionsSignal],
    placements: List[PlacementsSignal],
    dynamic_signals: List[DiscoveredSignal],
    requested_department: Optional[str] = None,
):
    if current_user and requested_department:
        if not AccessPolicyRepository.user_can_access_department(
            current_user, institution_id, requested_department
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Access denied: your department/program permission scope does not allow "
                    f"accessing department/program '{requested_department}'."
                ),
            )
    allowed_depts = (
        AccessPolicyRepository.get_allowed_departments_filter(current_user, institution_id)
        if current_user
        else None
    )
    if allowed_depts is not None:
        allowed_set = set(allowed_depts)
        cet = [s for s in cet if (s.department or "").strip().upper() in allowed_set]
        admissions = [s for s in admissions if (s.department or "").strip().upper() in allowed_set]
        placements = [s for s in placements if (s.department or "").strip().upper() in allowed_set]
        dynamic_signals = [
            ds for ds in dynamic_signals
            if (ds.context.department or "").strip().upper() in allowed_set
            or (ds.context.program or "").strip().upper() in allowed_set
        ]
    if requested_department:
        req_up = requested_department.strip().upper()
        cet = [s for s in cet if (s.department or "").strip().upper() == req_up]
        admissions = [s for s in admissions if (s.department or "").strip().upper() == req_up]
        placements = [s for s in placements if (s.department or "").strip().upper() == req_up]
        dynamic_signals = [
            ds for ds in dynamic_signals
            if (ds.context.department or "").strip().upper() == req_up
            or (ds.context.program or "").strip().upper() == req_up
        ]
    return cet, admissions, placements, dynamic_signals


async def _load_institutional_signals(
    session: AsyncSession,
    institution_id: str,
    current_user: Optional[UserModel] = None,
    requested_department: Optional[str] = None,
):
    inst = await InstitutionRepository.get_by_id(session, institution_id)
    if inst and current_user and not InstitutionRepository.user_can_access(inst, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: institution '{institution_id}' belongs to another user or organization.",
        )

    snapshots = await SignalSnapshotRepository.get_by_institution(session, institution_id)
    dynamic_signals = await DiscoveredSignalRepository.get_by_institution(session, institution_id)
    if not snapshots and not dynamic_signals:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No signal records found for institution '{institution_id}'. Ingest data first."
        )

    admissions: List[AdmissionsSignal] = []
    placements: List[PlacementsSignal] = []
    cet: List[CETRankingSignal] = []

    for s in snapshots:
        data = json.loads(s.payload_json)
        if s.signal_type == "admissions":
            admissions.append(AdmissionsSignal.model_validate(data))
        elif s.signal_type == "placements":
            placements.append(PlacementsSignal.model_validate(data))
        elif s.signal_type == "cet_ranking":
            cet.append(CETRankingSignal.model_validate(data))

    cet, admissions, placements, dynamic_signals = _apply_department_scope_filter(
        institution_id=institution_id,
        current_user=current_user,
        cet=cet,
        admissions=admissions,
        placements=placements,
        dynamic_signals=dynamic_signals,
        requested_department=requested_department,
    )
    return cet, admissions, placements, dynamic_signals


@router.get("", response_model=List[InstitutionOut])
async def list_institutions(
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user)
):
    insts = await InstitutionRepository.list_for_user(session, current_user)
    out: List[InstitutionOut] = []
    for i in insts:
        domains_list: List[str] = []
        if i.academic_domains_json:
            try:
                domains_list = json.loads(i.academic_domains_json)
            except Exception:
                domains_list = []
        if not domains_list and i.academic_domain:
            domains_list = [d.strip() for d in i.academic_domain.split(",") if d.strip()]
        out.append(
            InstitutionOut(
                id=i.id,
                name=i.name,
                state=i.state,
                city=i.city,
                accreditation_grade=i.accreditation_grade,
                entity_category=i.entity_category or "educational_institution",
                entity_category_other=i.entity_category_other,
                entity_type=i.entity_type or "institution",
                entity_type_other=i.entity_type_other,
                ownership_governance=i.ownership_governance,
                ownership_governance_other=i.ownership_governance_other,
                education_level=i.education_level,
                education_entity_type=i.education_entity_type or i.education_level,
                education_entity_type_other=i.education_entity_type_other,
                university_type=i.university_type,
                university_type_other=i.university_type_other,
                academic_domain=i.academic_domain,
                academic_domains=domains_list,
                academic_domain_other=i.academic_domain_other,
                parent_organization_id=i.parent_organization_id,
                organization_id=i.organization_id,
                owner_user_id=i.owner_user_id,
            )
        )
    return out


@router.get("/{institution_id}/overview", response_model=OverviewResponse)
async def get_institution_overview(
    institution_id: str,
    department: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """
    High-Performance Overview Endpoint (CIP Phase 2 Sections 21, 36, 50).
    Answers the 5 core executive questions:
    1. What is happening? (Institutional status & CRI)
    2. What changed? (Longitudinal change detection)
    3. What needs attention? (Prioritized operational findings)
    4. Supporting evidence? (7-field provenance links)
    5. What could happen next? (Forward trajectory outlook)
    Provides semantically distinct counts:
    changed_count != findings_count != evidence_count != observations_count.
    Reuses persisted analysis state without duplicate computation or DB writes.
    """
    return await AnalysisPersistenceService.get_or_compute_overview(
        session=session,
        institution_id=institution_id,
        current_user=current_user,
        department=department,
    )


@router.get("/{institution_id}/evaluate", response_model=CrisisAssessment)
async def evaluate_institution(
    institution_id: str,
    department: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user)
):
    cet, admissions, placements, dynamic_signals = await _load_institutional_signals(
        session, institution_id, current_user, department
    )
    org_id = await _resolve_org_id(session, institution_id, current_user)

    assessment = await AnalysisPersistenceService.get_or_evaluate_assessment(
        session=session,
        institution_id=institution_id,
        cet=cet,
        admissions=admissions,
        placements=placements,
        dynamic_signals=dynamic_signals,
        org_id=org_id,
    )
    return assessment


@router.get("/{institution_id}/dossier", response_model=InstitutionalDossier)
async def get_institution_dossier(
    institution_id: str,
    department: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user)
):
    cet, admissions, placements, dynamic_signals = await _load_institutional_signals(
        session, institution_id, current_user, department
    )
    org_id = await _resolve_org_id(session, institution_id, current_user)
    ingestion_records = await IngestionRecordRepository.list_by_institution(session, institution_id)
    dq_issues: List[DataQualityIssue] = []
    for rec in ingestion_records:
        dq_issues.extend(rec.data_quality_issues)

    assessment = await AnalysisPersistenceService.get_or_evaluate_assessment(
        session=session,
        institution_id=institution_id,
        cet=cet,
        admissions=admissions,
        placements=placements,
        dynamic_signals=dynamic_signals,
        org_id=org_id,
    )

    sources = ["ACID_PERSISTENCE_STORE"]
    for s in dynamic_signals:
        src_ref = f"{s.provenance.document}:{s.provenance.format_type}"
        if src_ref not in sources:
            sources.append(src_ref)

    assembler = EvidenceAssembler()
    dossier = assembler.assemble_dossier(
        assessment,
        signal_sources=sources,
        cet_history=cet,
        admissions_history=admissions,
        placements_history=placements,
        dynamic_signals=dynamic_signals,
        data_quality_issues=dq_issues,
    )
    dossier.organization_id = org_id
    return dossier



def _compute_institutional_slopes(
    dept_features: dict,
    admissions_history: List[AdmissionsSignal]
) -> Dict[str, float]:
    """Compute deterministic intake-weighted slopes across all departments (with normalized rank slope)."""
    if not dept_features:
        return {"vacancy_rate_slope": 0.0, "placement_pct_slope": 0.0, "closing_rank_slope": 0.0}
    if len(dept_features) == 1:
        fv = next(iter(dept_features.values()))
        return {
            "vacancy_rate_slope": fv.vacancy_rate_slope,
            "placement_pct_slope": fv.placement_pct_slope,
            "closing_rank_slope": round(fv.closing_rank_slope / 1000.0, 4),
        }

    total_intake = 0.0
    vac_slope_sum = 0.0
    plc_slope_sum = 0.0
    rnk_slope_sum = 0.0

    for dept_name in sorted(dept_features.keys()):
        fv = dept_features[dept_name]
        d_adm = [s for s in admissions_history if s.department == dept_name]
        weight = float(sorted(d_adm, key=lambda s: s.academic_year)[-1].sanctioned_intake) if d_adm else 60.0
        total_intake += weight
        vac_slope_sum += fv.vacancy_rate_slope * weight
        plc_slope_sum += fv.placement_pct_slope * weight
        rnk_slope_sum += (fv.closing_rank_slope / 1000.0) * weight

    if total_intake <= 0:
        return {"vacancy_rate_slope": 0.0, "placement_pct_slope": 0.0, "closing_rank_slope": 0.0}

    return {
        "vacancy_rate_slope": round(vac_slope_sum / total_intake, 6),
        "placement_pct_slope": round(plc_slope_sum / total_intake, 4),
        "closing_rank_slope": round(rnk_slope_sum / total_intake, 4),
    }


# Caches to prevent duplicate processing & repeated clicks (Part 16 & 29: Caching & Idempotency)
_RISK_TREND_CACHE: Dict[str, Dict[str, Any]] = {}
_PREDICTION_CACHE: Dict[str, Dict[str, Any]] = {}


@router.post("/{institution_id}/generate-risk-trend", response_model=RiskTrendResponse)
@router.get("/{institution_id}/risk-trend", response_model=RiskTrendResponse)
async def generate_or_get_risk_trend(
    institution_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """
    Generate or retrieve empirical risk trend across historical periods (Part 24: Risk Trend).
    - Validates dataset depth (requires >= 2 distinct historical periods).
    - Aggregates period-by-period CRI points and component metrics.
    - Projects 3-year autoregressive forward trajectory.
    - Caches results for fast, idempotent responses.
    """
    cet, admissions, placements, dynamic_signals = await _load_signals_allow_sparse(
        session, institution_id, current_user
    )

    # Collect distinct academic years
    years_set = set()
    for c in cet:
        if c.academic_year:
            years_set.add(c.academic_year)
    for a in admissions:
        if a.academic_year:
            years_set.add(a.academic_year)
    for p in placements:
        if p.graduation_year:
            years_set.add(p.graduation_year)
    for d in dynamic_signals:
        if d.context and d.context.academic_year:
            years_set.add(d.context.academic_year)

    distinct_years = sorted(list(years_set))
    num_periods = len(distinct_years)

    # Safe validation against insufficient data (never fabricate trend data)
    if num_periods < 2:
        return RiskTrendResponse(
            institution_id=institution_id,
            status="INSUFFICIENT_DATA",
            message=f"Generating a risk trend requires at least 2 dated historical periods; found {num_periods} period(s).",
            distinct_periods=num_periods,
            trend_direction="INSUFFICIENT_DATA",
            historical_points=[],
            projected_trajectory=[],
            human_summary="Insufficient historical data to calculate an empirical risk trend. Please upload at least 2 academic years of records.",
        )

    # Check cache if signals have not changed
    cache_key = f"{institution_id}_{len(cet)}_{len(admissions)}_{len(placements)}_{len(dynamic_signals)}"
    if cache_key in _RISK_TREND_CACHE:
        return RiskTrendResponse(**_RISK_TREND_CACHE[cache_key])

    engine = CrisisIntelligenceEngine()
    predictor = TrajectoryPredictor()
    historical_points: List[RiskTrendPoint] = []

    # Calculate period-by-period points
    for yr in distinct_years:
        sub_cet = [c for c in cet if c.academic_year == yr]
        sub_adm = [a for a in admissions if a.academic_year == yr]
        sub_plc = [p for p in placements if p.graduation_year == yr]
        sub_dyn = [d for d in dynamic_signals if d.context and d.context.academic_year == yr]

        period_eval = engine.evaluate_institution(
            institution_id=institution_id,
            cet_history=sub_cet,
            admissions_history=sub_adm,
            placements_history=sub_plc,
            dynamic_signals=sub_dyn,
        )

        avg_vac = (sum(a.vacancy_rate for a in sub_adm) / len(sub_adm)) if sub_adm else None
        avg_plc = (sum(p.placement_percentage for p in sub_plc) / len(sub_plc)) if sub_plc else None

        historical_points.append(
            RiskTrendPoint(
                academic_year=yr,
                period_label=f"AY {yr}",
                composite_risk_index=round(period_eval.composite_risk_index, 3),
                risk_level=period_eval.risk_level,
                primary_threat=period_eval.primary_driving_signal,
                admissions_vacancy_rate=round(avg_vac, 4) if avg_vac is not None else None,
                placement_percentage=round(avg_plc, 2) if avg_plc is not None else None,
                anomalies_count=len(period_eval.anomalies_detected),
            )
        )

    # Overall direction
    first_cri = historical_points[0].composite_risk_index
    latest_cri = historical_points[-1].composite_risk_index
    cri_delta = round(latest_cri - first_cri, 3)

    if cri_delta > 0.03:
        direction = "WORSENING"
        trend_text = f"increased by {cri_delta:+.3f} (worsening risk posture)"
    elif cri_delta < -0.03:
        direction = "IMPROVING"
        trend_text = f"decreased by {abs(cri_delta):.3f} (improving stability)"
    else:
        direction = "STABLE"
        trend_text = "remained largely stable within normal bounds"

    # Forward projections
    dept_features = extract_institutional_features(cet, admissions, placements)
    slopes = _compute_institutional_slopes(dept_features, admissions)
    trajectory = predictor.predict_trajectory(
        current_cri=latest_cri,
        feature_slopes=slopes,
        years_forward=3,
    )

    year_range_str = f"AY {distinct_years[0]} to AY {distinct_years[-1]}"
    human_summary = (
        f"Over the {num_periods}-period timeline ({year_range_str}), institutional risk {trend_text}, "
        f"moving from CRI {first_cri:.3f} to {latest_cri:.3f}. Primary threat driver: {historical_points[-1].primary_threat}."
    )

    resp_dict = {
        "institution_id": institution_id,
        "status": "SUCCESS",
        "message": f"Successfully generated risk trend across {num_periods} historical periods.",
        "distinct_periods": num_periods,
        "trend_direction": direction,
        "historical_points": [(p.model_dump() if hasattr(p, "model_dump") else p) for p in historical_points],
        "projected_trajectory": [(t.model_dump() if hasattr(t, "model_dump") else t) for t in trajectory],
        "human_summary": human_summary,
    }
    _RISK_TREND_CACHE[cache_key] = resp_dict
    return RiskTrendResponse(**resp_dict)


@router.post("/{institution_id}/predict", response_model=PredictionResponse)
@router.get("/{institution_id}/prediction", response_model=PredictionResponse)
async def generate_or_get_prediction(
    institution_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """
    Generate or retrieve 3-year autoregressive forward risk prediction (Part 25: Predictions).
    - Requires >= 2 historical periods; never fabricates forward predictions from insufficient history.
    - Returns human-first headline, summary, why it matters, and what to check.
    - Caches results for fast, idempotent responses.
    """
    cet, admissions, placements, dynamic_signals = await _load_institutional_signals(session, institution_id, current_user)

    years_set = set()
    for c in cet:
        if c.academic_year:
            years_set.add(c.academic_year)
    for a in admissions:
        if a.academic_year:
            years_set.add(a.academic_year)
    for p in placements:
        if p.graduation_year:
            years_set.add(p.graduation_year)
    for d in dynamic_signals:
        if d.context and d.context.academic_year:
            years_set.add(d.context.academic_year)

    distinct_years = sorted(list(years_set))
    num_periods = len(distinct_years)

    if num_periods < 2:
        return PredictionResponse(
            institution_id=institution_id,
            status="INSUFFICIENT_EVIDENCE",
            headline="Insufficient historical data for forward prediction",
            summary="CIP requires at least 2 distinct historical periods to compute trend momentum without fabricating assumptions.",
            why_it_matters="Forward projection without historical slope produces arbitrary mathematical speculation.",
            what_to_check="Upload institutional data covering at least two consecutive academic years to unlock trajectory modeling.",
            method_used="Autoregressive Trajectory Model",
            confidence=0.0,
            horizon_years=3,
            current_cri=0.0,
            projections=[],
            historical_periods_used=distinct_years,
        )

    cache_key = f"pred_{institution_id}_{len(cet)}_{len(admissions)}_{len(placements)}_{len(dynamic_signals)}"
    if cache_key in _PREDICTION_CACHE:
        return PredictionResponse(**_PREDICTION_CACHE[cache_key])

    engine = CrisisIntelligenceEngine()
    assessment = engine.evaluate_institution(
        institution_id=institution_id,
        cet_history=cet,
        admissions_history=admissions,
        placements_history=placements,
        dynamic_signals=dynamic_signals,
    )

    dept_features = extract_institutional_features(cet, admissions, placements)
    predictor = TrajectoryPredictor()
    slopes = _compute_institutional_slopes(dept_features, admissions)

    current_cri = round(assessment.composite_risk_index, 3)
    trajectory = predictor.predict_trajectory(
        current_cri=current_cri,
        feature_slopes=slopes,
        years_forward=3,
    )
    y3_cri = trajectory[-1].projected_cri if trajectory else current_cri
    cri_shift = round(y3_cri - current_cri, 3)

    if cri_shift > 0.03:
        headline = "Risk is projected to increase over the next 3 years under status-quo operations."
        summary = (
            f"The Composite Risk Index is projected to rise from {current_cri:.3f} to {y3_cri:.3f} "
            f"over the next 3 academic years if current trends in {assessment.primary_driving_signal} persist."
        )
        why_it_matters = (
            f"Unfavorable momentum in {assessment.primary_driving_signal} compounds over time, "
            f"increasing institutional vulnerability across subsequent admissions cycles."
        )
        what_to_check = "Implement targeted corrective interventions in admissions intake and placement outreach."
    elif cri_shift < -0.03:
        headline = "Risk is projected to decline over the next 3 years under current positive momentum."
        summary = (
            f"The Composite Risk Index is projected to improve from {current_cri:.3f} to {y3_cri:.3f} "
            f"over the next 3 academic years."
        )
        why_it_matters = "Positive trends in operational indicators are actively stabilizing institutional performance."
        what_to_check = "Maintain current governance policies and monitor departments showing isolated variance."
    else:
        headline = "Risk is projected to remain stable over the next 3 years."
        summary = f"The Composite Risk Index is projected to remain steady around {current_cri:.3f} across the 3-year outlook."
        why_it_matters = "Operational signals reflect balanced stability with no severe multi-period deterioration."
        what_to_check = "Continue standard periodic monitoring of emerging signals."

    resp_dict = {
        "institution_id": institution_id,
        "status": "SUCCESS",
        "headline": headline,
        "summary": summary,
        "why_it_matters": why_it_matters,
        "what_to_check": what_to_check,
        "method_used": "Autoregressive Trajectory Momentum (AR-1)",
        "confidence": round(assessment.confidence_score, 2),
        "horizon_years": 3,
        "current_cri": current_cri,
        "projections": [(t.model_dump() if hasattr(t, "model_dump") else t) for t in trajectory],
        "historical_periods_used": distinct_years,
    }
    _PREDICTION_CACHE[cache_key] = resp_dict
    return PredictionResponse(**resp_dict)





@router.get("/{institution_id}/report", response_model=ExecutiveNarrativeResponse)
async def get_institution_report(
    institution_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user)
):
    cet, admissions, placements, dynamic_signals = await _load_institutional_signals(session, institution_id, current_user)
    org_id = await _resolve_org_id(session, institution_id, current_user)
    ingestion_records = await IngestionRecordRepository.list_by_institution(session, institution_id)
    dq_issues: List[DataQualityIssue] = []
    for rec in ingestion_records:
        dq_issues.extend(rec.data_quality_issues)

    engine = CrisisIntelligenceEngine()
    assessment = engine.evaluate_institution(
        institution_id=institution_id,
        cet_history=cet,
        admissions_history=admissions,
        placements_history=placements,
        dynamic_signals=dynamic_signals,
    )
    assessment.organization_id = org_id

    sources = ["ACID_PERSISTENCE_STORE"]
    for s in dynamic_signals:
        src_ref = f"{s.provenance.document}:{s.provenance.format_type}"
        if src_ref not in sources:
            sources.append(src_ref)

    assembler = EvidenceAssembler()
    dossier = assembler.assemble_dossier(
        assessment,
        signal_sources=sources,
        cet_history=cet,
        admissions_history=admissions,
        placements_history=placements,
        dynamic_signals=dynamic_signals,
        data_quality_issues=dq_issues,
    )
    dossier.organization_id = org_id

    p4_engine = ExplainableForecastingAndMemoryEngine()
    forecast = p4_engine.generate_explainable_forecast(
        institution_id=institution_id,
        cet_history=cet,
        admissions_history=admissions,
        placements_history=placements,
        dynamic_signals=dynamic_signals,
        years_forward=3,
    )
    forecast.organization_id = org_id

    reasoner = LLMStructuredReasoner()
    report = reasoner.generate_narrative(dossier, forecast=forecast)
    report.organization_id = org_id
    return report


@router.get("/{institution_id}/ai-executive-analysis", response_model=ExecutiveNarrativeResponse)
async def get_ai_executive_analysis(
    institution_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """
    CIP Phase 5 AI Executive Analysis endpoint.
    Replaces 'Grounded Executive Briefing' with 'AI Executive Analysis' structured as:
    - What is happening?
    - Why?
    - Evidence (7-field provenance)
    - What could happen next? (deterministic forecast)
    - What should leadership investigate?
    """
    return await get_institution_report(institution_id=institution_id, session=session, current_user=current_user)


@router.get("/{institution_id}/report/pdf")
async def get_institution_pdf_report(
    institution_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user)
):
    """
    Generate an audit-grade binary PDF report for the specified institution.
    Includes CRI metrics, anomaly evidence tokens, trajectory, and executive narrative.
    """
    cet, admissions, placements, dynamic_signals = await _load_institutional_signals(session, institution_id, current_user)
    org_id = await _resolve_org_id(session, institution_id, current_user)

    engine = CrisisIntelligenceEngine()
    assessment = engine.evaluate_institution(
        institution_id=institution_id,
        cet_history=cet,
        admissions_history=admissions,
        placements_history=placements,
        dynamic_signals=dynamic_signals,
    )
    assessment.organization_id = org_id

    sources = ["ACID_PERSISTENCE_STORE"]
    for s in dynamic_signals:
        src_ref = f"{s.provenance.document}:{s.provenance.format_type}"
        if src_ref not in sources:
            sources.append(src_ref)

    assembler = EvidenceAssembler()
    dossier = assembler.assemble_dossier(
        assessment,
        signal_sources=sources,
        cet_history=cet,
        admissions_history=admissions,
        placements_history=placements,
        dynamic_signals=dynamic_signals,
    )
    dossier.organization_id = org_id

    p4_engine = ExplainableForecastingAndMemoryEngine()
    forecast = p4_engine.generate_explainable_forecast(
        institution_id=institution_id,
        cet_history=cet,
        admissions_history=admissions,
        placements_history=placements,
        dynamic_signals=dynamic_signals,
        years_forward=3,
    )
    forecast.organization_id = org_id

    reasoner = LLMStructuredReasoner()
    narrative = reasoner.generate_narrative(dossier, forecast=forecast)
    narrative.organization_id = org_id

    dept_features = extract_institutional_features(cet, admissions, placements)
    predictor = TrajectoryPredictor()
    slopes = _compute_institutional_slopes(dept_features, admissions)
    trajectory = predictor.predict_trajectory(
        current_cri=assessment.composite_risk_index,
        feature_slopes=slopes,
        years_forward=3
    )

    pdf_bytes = generate_crisis_pdf(dossier=dossier, narrative=narrative, trajectory=trajectory)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="criss_report_{institution_id}.pdf"'
        }
    )


async def _build_phase3_intelligence_report(
    session: AsyncSession,
    institution_id: str,
    current_user: UserModel,
) -> InstitutionalIntelligenceReport:
    inst = await InstitutionRepository.get_by_id(session, institution_id)
    if inst and not InstitutionRepository.user_can_access(inst, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: institution '{institution_id}' belongs to another user or organization.",
        )

    snapshots = await SignalSnapshotRepository.get_by_institution(session, institution_id)
    dynamic_signals = await DiscoveredSignalRepository.get_by_institution(session, institution_id)
    ingestion_records = await IngestionRecordRepository.list_by_institution(session, institution_id)

    if not snapshots and not dynamic_signals and not ingestion_records:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No signal or ingestion records found for institution '{institution_id}'. Ingest data first.",
        )

    admissions: List[AdmissionsSignal] = []
    placements: List[PlacementsSignal] = []
    cet: List[CETRankingSignal] = []
    for s in snapshots:
        data = json.loads(s.payload_json)
        if s.signal_type == "admissions":
            admissions.append(AdmissionsSignal.model_validate(data))
        elif s.signal_type == "placements":
            placements.append(PlacementsSignal.model_validate(data))
        elif s.signal_type == "cet_ranking":
            cet.append(CETRankingSignal.model_validate(data))

    cet, admissions, placements, dynamic_signals = _apply_department_scope_filter(
        institution_id, current_user, cet, admissions, placements, dynamic_signals
    )

    dq_issues: List[DataQualityIssue] = []
    for rec in ingestion_records:
        dq_issues.extend(rec.data_quality_issues)

    latest_assess_row = await AssessmentRepository.get_latest(session, institution_id)
    prev_assessments: List[CrisisAssessment] = []
    if latest_assess_row:
        try:
            prev_assessments.append(CrisisAssessment.model_validate_json(latest_assess_row.assessment_json))
        except Exception:
            pass

    intel_engine = InstitutionalIntelligenceEngine()
    report = intel_engine.analyze_institution(
        institution_id=institution_id,
        cet_history=cet,
        admissions_history=admissions,
        placements_history=placements,
        dynamic_signals=dynamic_signals,
        previous_assessments=prev_assessments,
        data_quality_issues=dq_issues,
    )
    report.organization_id = await _resolve_org_id(session, institution_id, current_user)
    return report


@router.get("/{institution_id}/intelligence", response_model=InstitutionalIntelligenceReport)
async def get_institutional_intelligence(
    institution_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """
    Return the full CIP Phase 3 Institutional Intelligence Report:
    - Epistemic separation (OBSERVED_FACT, ANALYSIS, INFERENCE, PREDICTION, RECOMMENDATION, UNKNOWN_INSUFFICIENT_EVIDENCE)
    - Institution-specific historical baselines
    - Multi-dimensional evaluated anomalies
    - 5-stage risk progression (Observation -> Anomaly -> Emerging Risk -> Institutional Risk -> Crisis)
    - Cross-signal intelligence (potential contributing factors vs causation)
    - Investigation objects
    - Early warning progression
    - What-changed state comparison
    """
    return await _build_phase3_intelligence_report(session, institution_id, current_user)


@router.get("/{institution_id}/baselines", response_model=List[HistoricalBaseline])
async def get_institutional_baselines(
    institution_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """Return institution-specific historical baselines (never fabricated when history is insufficient)."""
    report = await _build_phase3_intelligence_report(session, institution_id, current_user)
    return report.baselines


@router.get("/{institution_id}/anomalies", response_model=List[EvaluatedAnomaly])
async def get_institutional_anomalies(
    institution_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """Return multi-dimensional evaluated anomalies with 6-part explanations."""
    report = await _build_phase3_intelligence_report(session, institution_id, current_user)
    return report.evaluated_anomalies


@router.get("/{institution_id}/risk-progression", response_model=RiskProgressionLadder)
async def get_institutional_risk_progression(
    institution_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """Return the 5-stage risk progression ladder (Observation -> Anomaly -> Emerging Risk -> Institutional Risk -> Crisis)."""
    report = await _build_phase3_intelligence_report(session, institution_id, current_user)
    return report.risk_progression


@router.get("/{institution_id}/cross-signals", response_model=List[CrossSignalFinding])
async def get_institutional_cross_signals(
    institution_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """Return cross-signal intelligence findings distinguishing correlation from causation."""
    report = await _build_phase3_intelligence_report(session, institution_id, current_user)
    return report.cross_signal_findings


@router.get("/{institution_id}/investigations", response_model=List[InvestigationObject])
async def get_institutional_investigations(
    institution_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """Return structured InvestigationObjects for major findings."""
    report = await _build_phase3_intelligence_report(session, institution_id, current_user)
    return report.investigations


@router.get("/{institution_id}/early-warnings", response_model=List[EarlyWarningIndicator])
async def get_institutional_early_warnings(
    institution_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """Return early warning indicators tracking weak_signal -> repeated_anomaly -> cross_signal_confirmation -> emerging_risk."""
    report = await _build_phase3_intelligence_report(session, institution_id, current_user)
    return report.early_warnings


@router.get("/{institution_id}/what-changed", response_model=WhatChangedReport)
async def get_institutional_what_changed(
    institution_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """Return state comparison report (new/worsening/improving signals, resolved/new risks, changed relationships, changed forecasts)."""
    report = await _build_phase3_intelligence_report(session, institution_id, current_user)
    return report.what_changed


async def _load_signals_allow_sparse(
    session: AsyncSession,
    institution_id: str,
    current_user: UserModel,
):
    """
    Load signals for an institution while allowing sparse (0 or 1 period) datasets
    so that the forecast endpoint can return an explicit INSUFFICIENT_EVIDENCE state
    when an institution exists or has a single-period ingestion record.
    """
    inst = await InstitutionRepository.get_by_id(session, institution_id)
    if inst and not InstitutionRepository.user_can_access(inst, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: institution '{institution_id}' belongs to another user or organization.",
        )

    snapshots = await SignalSnapshotRepository.get_by_institution(session, institution_id)
    dynamic_signals = await DiscoveredSignalRepository.get_by_institution(session, institution_id)
    ingestion_records = await IngestionRecordRepository.list_by_institution(session, institution_id)

    if not inst and not snapshots and not dynamic_signals and not ingestion_records:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Institution '{institution_id}' not found.",
        )

    admissions: List[AdmissionsSignal] = []
    placements: List[PlacementsSignal] = []
    cet: List[CETRankingSignal] = []
    for s in snapshots:
        data = json.loads(s.payload_json)
        if s.signal_type == "admissions":
            admissions.append(AdmissionsSignal.model_validate(data))
        elif s.signal_type == "placements":
            placements.append(PlacementsSignal.model_validate(data))
        elif s.signal_type == "cet_ranking":
            cet.append(CETRankingSignal.model_validate(data))

    return _apply_department_scope_filter(
        institution_id, current_user, cet, admissions, placements, dynamic_signals
    )


@router.get("/{institution_id}/forecast", response_model=ExplainableForecast)
async def get_explainable_forecast(
    institution_id: str,
    years_forward: int = 3,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """
    CIP Phase 4 Explainable Forecast endpoint.
    Exposes prediction, horizon, method_actually_used, input_signals, historical_evidence,
    data_coverage, confidence, limitations, alternative_explanations, trajectory_drivers,
    and why_did_prediction_change.
    Returns status='INSUFFICIENT_EVIDENCE' with prediction=None when < 2 historical periods exist.
    """
    cet, admissions, placements, dynamic_signals = await _load_signals_allow_sparse(
        session, institution_id, current_user
    )
    org_id = await _resolve_org_id(session, institution_id, current_user)

    prior_preds = await InstitutionalMemoryRepository.list_by_institution(
        session, institution_id, category=MemoryCategory.PREDICTION.value
    )
    prev_payload = prior_preds[-1].payload if prior_preds else None

    p4_engine = ExplainableForecastingAndMemoryEngine()
    forecast = p4_engine.generate_explainable_forecast(
        institution_id=institution_id,
        cet_history=cet,
        admissions_history=admissions,
        placements_history=placements,
        dynamic_signals=dynamic_signals,
        years_forward=years_forward,
        previous_prediction_payload=prev_payload,
    )
    forecast.organization_id = org_id

    # Persist the prediction or insufficient-evidence unknown into institutional memory
    if forecast.status == ForecastStatus.SUFFICIENT_EVIDENCE and forecast.prediction:
        terminal = forecast.prediction[-1]
        pred_entry = InstitutionalMemoryEntry(
            memory_id=f"mem_pred_{institution_id}_{terminal.target_period or terminal.year_offset}",
            institution_id=institution_id,
            organization_id=org_id,
            category=MemoryCategory.PREDICTION,
            domain="institutional_forecasting",
            metric_or_topic="composite_risk_index",
            academic_year=terminal.target_period,
            statement=(
                f"Explainable Forecast ({forecast.horizon}): CRI projected from {forecast.current_cri:.4f} "
                f"to {terminal.projected_cri:.4f} (confidence {forecast.confidence:.2f})."
            ),
            confidence=forecast.confidence,
            evidence_refs=forecast.historical_evidence[:3],
            payload=forecast.model_dump(mode="json"),
        )
        await InstitutionalMemoryRepository.upsert_entry(
            session=session,
            entry=pred_entry,
            organization_id=org_id,
            user_id=current_user.id,
        )
    elif forecast.insufficient_evidence_reason:
        unk_entry = InstitutionalMemoryEntry(
            memory_id=f"mem_unk_{institution_id}_forecast_insufficient_history",
            institution_id=institution_id,
            organization_id=org_id,
            category=MemoryCategory.UNKNOWN,
            domain="institutional_forecasting",
            metric_or_topic="composite_risk_index_forecast",
            academic_year=None,
            statement=forecast.insufficient_evidence_reason,
            confidence=0.0,
            evidence_refs=[],
            payload={"status": forecast.status.value, "coverage": forecast.data_coverage.model_dump(mode="json")},
        )
        await InstitutionalMemoryRepository.upsert_entry(
            session=session,
            entry=unk_entry,
            organization_id=org_id,
            user_id=current_user.id,
        )

    return forecast


@router.get("/{institution_id}/forecast/why-changed", response_model=ForecastChangeExplanation)
async def get_why_prediction_changed(
    institution_id: str,
    years_forward: int = 3,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """
    Answer: 'Why did the prediction change?'
    Deterministically identifies actual changed inputs (current_cri, feature slopes, newly ingested periods)
    and their exact mathematical contribution to the projected CRI delta.
    """
    forecast = await get_explainable_forecast(
        institution_id=institution_id,
        years_forward=years_forward,
        session=session,
        current_user=current_user,
    )
    return forecast.why_did_prediction_change


async def _sync_and_build_memory_view(
    session: AsyncSession,
    institution_id: str,
    current_user: UserModel,
) -> InstitutionalMemoryStoreView:
    cet, admissions, placements, dynamic_signals = await _load_signals_allow_sparse(
        session, institution_id, current_user
    )
    org_id = await _resolve_org_id(session, institution_id, current_user)

    p4_engine = ExplainableForecastingAndMemoryEngine()
    intel_report: Optional[InstitutionalIntelligenceReport] = None
    if cet or admissions or placements or dynamic_signals:
        intel_report = await _build_phase3_intelligence_report(session, institution_id, current_user)
        prior_preds = await InstitutionalMemoryRepository.list_by_institution(
            session, institution_id, category=MemoryCategory.PREDICTION.value
        )
        prev_payload = prior_preds[-1].payload if prior_preds else None
        forecast = p4_engine.generate_explainable_forecast(
            institution_id=institution_id,
            cet_history=cet,
            admissions_history=admissions,
            placements_history=placements,
            dynamic_signals=dynamic_signals,
            years_forward=3,
            previous_prediction_payload=prev_payload,
        )
        forecast.organization_id = org_id
        extracted_entries = p4_engine.extract_memory_entries_from_intelligence(
            intel_report=intel_report,
            forecast=forecast,
            cet_history=cet,
            admissions_history=admissions,
            placements_history=placements,
            dynamic_signals=dynamic_signals,
        )
        for e in extracted_entries:
            e.organization_id = org_id
        await InstitutionalMemoryRepository.save_entries_bulk(
            session=session,
            entries=extracted_entries,
            organization_id=org_id,
            user_id=current_user.id,
        )

    persisted_entries = await InstitutionalMemoryRepository.list_by_institution(session, institution_id)
    for pe in persisted_entries:
        if not pe.organization_id:
            pe.organization_id = org_id
    assess_rows = await AssessmentRepository.list_by_institution(session, institution_id)
    prev_assessments: List[CrisisAssessment] = []
    for r in assess_rows:
        try:
            prev_assessments.append(CrisisAssessment.model_validate_json(r.assessment_json))
        except Exception:
            pass

    mem_view = p4_engine.build_memory_store_view(
        institution_id=institution_id,
        persisted_entries=persisted_entries,
        intel_report=intel_report,
        previous_assessments=prev_assessments,
    )
    mem_view.organization_id = org_id
    return mem_view


@router.get("/{institution_id}/memory", response_model=InstitutionalMemoryStoreView)
async def get_institutional_memory(
    institution_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """
    Return the CIP Phase 4 Institutional Memory store for the institution,
    exposing distinct categories (observed_fact, analysis, inference, prediction,
    outcome, user_feedback, unknown) and all 8 longitudinal collections.
    """
    return await _sync_and_build_memory_view(session, institution_id, current_user)


@router.post("/{institution_id}/memory/sync", response_model=InstitutionalMemoryStoreView)
async def sync_institutional_memory_endpoint(
    institution_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """Explicitly synchronize current institutional intelligence, forecasts, and backtest outcomes into Institutional Memory."""
    return await _sync_and_build_memory_view(session, institution_id, current_user)


@router.post("/{institution_id}/memory/outcomes", response_model=PredictionOutcomeComparison)
async def record_prediction_outcome_comparison(
    institution_id: str,
    req: PredictionOutcomeComparisonRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """
    CIP Phase 4 Learning Loop endpoint:
    Prediction -> later observation -> outcome comparison -> prediction accuracy -> institutional learning.
    Persists an 'outcome' entry in Institutional Memory.
    """
    cet, admissions, placements, dynamic_signals = await _load_signals_allow_sparse(
        session, institution_id, current_user
    )
    org_id = await _resolve_org_id(session, institution_id, current_user)

    predicted_val = req.predicted_value
    band_lo = req.confidence_band_low
    band_hi = req.confidence_band_high
    pred_mem_id = req.prediction_memory_id

    if predicted_val is None:
        preds = await InstitutionalMemoryRepository.list_by_institution(
            session, institution_id, category=MemoryCategory.PREDICTION.value
        )
        if preds:
            latest_pred = preds[-1]
            pred_mem_id = pred_mem_id or latest_pred.memory_id
            pts = latest_pred.payload.get("prediction") or []
            matched_pt = next((p for p in pts if p.get("target_period") == req.target_academic_year), None)
            if not matched_pt and pts:
                matched_pt = pts[0]
            if matched_pt:
                predicted_val = float(matched_pt["projected_cri"])
                band_lo = float(matched_pt.get("confidence_band_low", max(0.0, predicted_val - 0.08)))
                band_hi = float(matched_pt.get("confidence_band_high", min(1.0, predicted_val + 0.08)))

    if predicted_val is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="predicted_value was not provided and no prior prediction was found in Institutional Memory.",
        )

    observed_val = req.later_observed_value
    if observed_val is None:
        if cet or admissions or placements or dynamic_signals:
            engine = CrisisIntelligenceEngine()
            assess = engine.evaluate_institution(
                institution_id=institution_id,
                cet_history=cet,
                admissions_history=admissions,
                placements_history=placements,
                dynamic_signals=dynamic_signals,
            )
            observed_val = assess.composite_risk_index
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="later_observed_value must be provided when no institutional signals are available.",
            )

    p4_engine = ExplainableForecastingAndMemoryEngine()
    comparison, outcome_entry = p4_engine.compare_prediction_with_outcome(
        institution_id=institution_id,
        target_academic_year=req.target_academic_year,
        metric_name=req.metric_name,
        predicted_value=predicted_val,
        later_observed_value=observed_val,
        confidence_band_low=band_lo,
        confidence_band_high=band_hi,
        prediction_memory_id=pred_mem_id,
        notes=req.notes,
    )
    outcome_entry.organization_id = org_id

    await InstitutionalMemoryRepository.upsert_entry(
        session=session,
        entry=outcome_entry,
        organization_id=org_id,
        user_id=current_user.id,
    )
    return comparison


@router.post("/{institution_id}/memory/feedback", response_model=UserFeedbackRecord)
async def submit_institutional_user_feedback(
    institution_id: str,
    submission: UserFeedbackSubmission,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """
    CIP Phase 4 User Feedback endpoint.
    Supports verdicts: 'Confirmed', 'Incorrect', 'Insufficient Evidence'.
    Strictly stores feedback under category 'user_feedback' (converted_to_observed_fact=False)
    and never blindly converts user feedback into observed_fact.
    """
    inst = await InstitutionRepository.get_by_id(session, institution_id)
    if inst and not InstitutionRepository.user_can_access(inst, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: institution '{institution_id}' belongs to another user or organization.",
        )
    org_id = await _resolve_org_id(session, institution_id, current_user)

    record = await InstitutionalMemoryRepository.save_user_feedback(
        session=session,
        institution_id=institution_id,
        submission=submission,
        user_id=current_user.id,
        organization_id=org_id,
    )
    return record


@router.get("/{institution_id}/evidence-provenance", response_model=EvidenceProvenanceCatalog)
async def get_institution_evidence_provenance(
    institution_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """
    CIP Phase 5 Evidence Provenance endpoint.
    Returns all major institutional insights/findings linked to their 7-field ProvenanceRecords:
    source, document, page_or_section, table_cell_or_range, excerpt_or_image,
    extraction_confidence, and date_or_context.
    """
    cet, admissions, placements, dynamic_signals = await _load_institutional_signals(session, institution_id, current_user)
    org_id = await _resolve_org_id(session, institution_id, current_user)
    ingestion_records = await IngestionRecordRepository.list_by_institution(session, institution_id)
    dq_issues: List[DataQualityIssue] = []
    for rec in ingestion_records:
        dq_issues.extend(rec.data_quality_issues)

    engine = CrisisIntelligenceEngine()
    assessment = engine.evaluate_institution(
        institution_id=institution_id,
        cet_history=cet,
        admissions_history=admissions,
        placements_history=placements,
        dynamic_signals=dynamic_signals,
    )
    assessment.organization_id = org_id

    sources = ["ACID_PERSISTENCE_STORE"]
    for s in dynamic_signals:
        src_ref = f"{s.provenance.document}:{s.provenance.format_type}"
        if src_ref not in sources:
            sources.append(src_ref)

    assembler = EvidenceAssembler()
    dossier = assembler.assemble_dossier(
        assessment,
        signal_sources=sources,
        cet_history=cet,
        admissions_history=admissions,
        placements_history=placements,
        dynamic_signals=dynamic_signals,
        data_quality_issues=dq_issues,
    )
    dossier.organization_id = org_id

    intel_report = await _build_phase3_intelligence_report(session, institution_id, current_user)
    p4_engine = ExplainableForecastingAndMemoryEngine()
    forecast = p4_engine.generate_explainable_forecast(
        institution_id=institution_id,
        cet_history=cet,
        admissions_history=admissions,
        placements_history=placements,
        dynamic_signals=dynamic_signals,
        years_forward=3,
    )
    forecast.organization_id = org_id

    catalog = assembler.build_provenance_catalog(
        institution_id=institution_id,
        dossier=dossier,
        intel_report=intel_report,
        forecast=forecast,
        cet_history=cet,
        admissions_history=admissions,
        placements_history=placements,
        dynamic_signals=dynamic_signals,
    )
    catalog.organization_id = org_id
    return catalog


@router.post("/{institution_id}/investigate-question", response_model=NaturalQuestionInvestigationResponse)
async def investigate_natural_question(
    institution_id: str,
    req: NaturalQuestionRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """
    CIP Phase 5 Natural-Language Grounded Investigation endpoint.
    Supports questions such as:
    - 'Why did retention decline?'
    - 'Why did placements fall?'
    - 'What changed since the last analysis?'
    Returns finding, 7-field evidence, related_signals, potential_contributing_factors,
    alternative_explanations, confidence, and missing_information using ONLY available evidence.
    """
    cet, admissions, placements, dynamic_signals = await _load_signals_allow_sparse(
        session, institution_id, current_user
    )
    org_id = await _resolve_org_id(session, institution_id, current_user)
    ingestion_records = await IngestionRecordRepository.list_by_institution(session, institution_id)
    dq_issues: List[DataQualityIssue] = []
    for rec in ingestion_records:
        dq_issues.extend(rec.data_quality_issues)

    engine = CrisisIntelligenceEngine()
    assessment = engine.evaluate_institution(
        institution_id=institution_id,
        cet_history=cet,
        admissions_history=admissions,
        placements_history=placements,
        dynamic_signals=dynamic_signals,
    )
    assessment.organization_id = org_id

    sources = ["ACID_PERSISTENCE_STORE"]
    for s in dynamic_signals:
        src_ref = f"{s.provenance.document}:{s.provenance.format_type}"
        if src_ref not in sources:
            sources.append(src_ref)

    assembler = EvidenceAssembler()
    dossier = assembler.assemble_dossier(
        assessment,
        signal_sources=sources,
        cet_history=cet,
        admissions_history=admissions,
        placements_history=placements,
        dynamic_signals=dynamic_signals,
        data_quality_issues=dq_issues,
    )
    dossier.organization_id = org_id

    latest_assess_row = await AssessmentRepository.get_latest(session, institution_id)
    prev_assessments: List[CrisisAssessment] = []
    if latest_assess_row:
        try:
            prev_assessments.append(CrisisAssessment.model_validate_json(latest_assess_row.assessment_json))
        except Exception:
            pass

    intel_engine = InstitutionalIntelligenceEngine()
    intel_report = intel_engine.analyze_institution(
        institution_id=institution_id,
        cet_history=cet,
        admissions_history=admissions,
        placements_history=placements,
        dynamic_signals=dynamic_signals,
        previous_assessments=prev_assessments,
        data_quality_issues=dq_issues,
    )
    intel_report.organization_id = org_id

    p4_engine = ExplainableForecastingAndMemoryEngine()
    prior_preds = await InstitutionalMemoryRepository.list_by_institution(
        session, institution_id, category=MemoryCategory.PREDICTION.value
    )
    prev_payload = prior_preds[-1].payload if prior_preds else None
    forecast = p4_engine.generate_explainable_forecast(
        institution_id=institution_id,
        cet_history=cet,
        admissions_history=admissions,
        placements_history=placements,
        dynamic_signals=dynamic_signals,
        years_forward=3,
        previous_prediction_payload=prev_payload,
    )
    forecast.organization_id = org_id

    inv_engine = EvidenceGroundedInvestigationEngine()
    inv_resp = inv_engine.investigate_question(
        institution_id=institution_id,
        question=req.question,
        dossier=dossier,
        intel_report=intel_report,
        forecast=forecast,
        cet_history=cet,
        admissions_history=admissions,
        placements_history=placements,
        dynamic_signals=dynamic_signals,
        data_quality_issues=dq_issues,
    )
    inv_resp.organization_id = org_id
    return inv_resp


@router.get("/{institution_id}/gemini-status", response_model=GeminiStatusResponse)
async def get_institution_gemini_status(
    institution_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """
    Return truthful runtime status distinguishing LIVE_GEMINI from DETERMINISTIC_FALLBACK.
    Never pretends Gemini is active when GEMINI_API_KEY is absent.
    """
    inst = await InstitutionRepository.get_by_id(session, institution_id)
    if inst and not InstitutionRepository.user_can_access(inst, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: institution '{institution_id}' belongs to another user or organization.",
        )
    reasoner = LLMStructuredReasoner()
    return reasoner.get_gemini_status()


# ═══════════════════════════════════════════════════════════════════════════════
# CIP PHASE 7: ORGANIZATION / NETWORK INTELLIGENCE & CROSS-INSTITUTION ROUTES
# ═══════════════════════════════════════════════════════════════════════════════


async def _load_organization_and_constituents(
    session: AsyncSession,
    organization_id: str,
    current_user: UserModel,
):
    org = await OrganizationRepository.get_by_id(session, organization_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Organization '{organization_id}' not found.",
        )
    if not OrganizationRepository.user_can_access(org, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: user does not have organization-level access to '{organization_id}'.",
        )
    constituents = await InstitutionRepository.list_constituent_institutions(session, organization_id, current_user)
    return org, constituents


async def _build_organization_network_report(
    session: AsyncSession,
    organization_id: str,
    current_user: UserModel,
) -> OrganizationNetworkIntelligenceReport:
    await _load_organization_and_constituents(session, organization_id, current_user)
    net_engine = OrganizationNetworkIntelligenceEngine()
    return await net_engine.build_organization_intelligence_report(
        session=session,
        organization_id=organization_id,
        current_user=current_user,
    )


@org_router.get("/{organization_id}/intelligence", response_model=OrganizationNetworkIntelligenceReport)
async def get_organization_intelligence(
    organization_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """
    CIP Phase 7 Organization / Network Intelligence endpoint.
    Computes and returns:
    - institution_count and breakdown (by type, risk level, risk stage, data availability)
    - data_coverage across constituent institutions
    - institution_level_risks (constituent summaries + department/program rollups)
    - common_emerging_patterns (strictly non-causal OBSERVED_CO_OCCURRENCE)
    - cross_institution_comparisons (where comparable evidence permits)
    """
    return await _build_organization_network_report(session, organization_id, current_user)


@router.get("/organization/{organization_id}/intelligence", response_model=OrganizationNetworkIntelligenceReport)
async def get_organization_intelligence_alias(
    organization_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """Alias for GET /api/v1/organizations/{organization_id}/intelligence."""
    return await _build_organization_network_report(session, organization_id, current_user)


@org_router.get("/{organization_id}/signals")
async def get_organization_signals_via_org_router(
    organization_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """Return organization-wide signal inventory across all constituent institutions."""
    org, constituents = await _load_organization_and_constituents(session, organization_id, current_user)
    institution_summaries = []
    total_canonical = 0
    total_dynamic = 0
    all_domains = set()
    all_years = set()

    for inst in constituents:
        snapshots = await SignalSnapshotRepository.get_by_institution(session, inst.id)
        dynamic_signals = await DiscoveredSignalRepository.get_by_institution(session, inst.id)

        admissions: List[AdmissionsSignal] = []
        placements: List[PlacementsSignal] = []
        cet: List[CETRankingSignal] = []
        for s in snapshots:
            data = json.loads(s.payload_json)
            if s.signal_type == "admissions":
                admissions.append(AdmissionsSignal.model_validate(data))
            elif s.signal_type == "placements":
                placements.append(PlacementsSignal.model_validate(data))
            elif s.signal_type == "cet_ranking":
                cet.append(CETRankingSignal.model_validate(data))

        cet, admissions, placements, dynamic_signals = _apply_department_scope_filter(
            inst.id, current_user, cet, admissions, placements, dynamic_signals
        )

        inst_canon = len(cet) + len(admissions) + len(placements)
        inst_dyn = len(dynamic_signals)
        total_canonical += inst_canon
        total_dynamic += inst_dyn

        inst_domains = set()
        inst_years = set()
        if admissions:
            inst_domains.add("admissions")
            for a in admissions:
                inst_years.add(a.academic_year)
        if placements:
            inst_domains.add("placements")
            for p in placements:
                inst_years.add(p.academic_year)
        if cet:
            inst_domains.add("cet_ranking")
            for c in cet:
                inst_years.add(c.academic_year)
        for d in dynamic_signals:
            if d.domain:
                inst_domains.add(d.domain.value if hasattr(d.domain, "value") else str(d.domain))
            yr = getattr(d.context, "academic_year", None) if hasattr(d, "context") else getattr(d, "academic_year", None)
            if yr:
                inst_years.add(yr)

        all_domains.update(inst_domains)
        all_years.update(inst_years)
        institution_summaries.append({
            "institution_id": inst.id,
            "institution_name": inst.name,
            "institution_type": inst.education_entity_type or inst.education_level or inst.entity_type,
            "canonical_signal_count": inst_canon,
            "discovered_signal_count": inst_dyn,
            "domains": sorted(inst_domains),
            "years_covered": sorted(inst_years),
        })

    return {
        "organization_id": org.id,
        "organization_name": org.name,
        "institution_count": len(constituents),
        "total_canonical_signals": total_canonical,
        "total_discovered_signals": total_dynamic,
        "domains_covered": sorted(all_domains),
        "years_covered": sorted(all_years),
        "institutions": institution_summaries,
    }


@org_router.get("/{organization_id}/evidence-provenance")
async def get_organization_evidence_provenance(
    organization_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """
    Aggregate evidence provenance across all constituent institutions in the organization,
    preserving institution_id and organization_id attribution on every finding.
    """
    org, constituents = await _load_organization_and_constituents(session, organization_id, current_user)
    institution_catalogs = []
    combined_items = []
    total_provenance_records = 0

    for inst in constituents:
        try:
            catalog = await get_institution_evidence_provenance(
                institution_id=inst.id,
                session=session,
                current_user=current_user,
            )
            cat_dict = catalog.model_dump(mode="json")
            cat_dict["institution_name"] = inst.name
            cat_dict["institution_type"] = inst.education_entity_type or inst.education_level or inst.entity_type
            institution_catalogs.append(cat_dict)
            total_provenance_records += catalog.total_provenance_records
            for item in cat_dict.get("items", []):
                item["institution_id"] = inst.id
                item["institution_name"] = inst.name
                item["organization_id"] = org.id
                combined_items.append(item)
        except HTTPException:
            # Constituent institution has no ingested signals yet
            continue

    return {
        "organization_id": org.id,
        "organization_name": org.name,
        "institution_count": len(constituents),
        "institutions_with_evidence": len(institution_catalogs),
        "total_provenance_records": total_provenance_records,
        "total_insights": len(combined_items),
        "items": combined_items,
        "institution_catalogs": institution_catalogs,
    }


@org_router.get("/{organization_id}/forecast")
async def get_organization_forecasts(
    organization_id: str,
    years_forward: int = 3,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """
    Return explainable forecasts for every constituent institution in the organization,
    respecting insufficient-evidence guardrails per institution.
    """
    org, constituents = await _load_organization_and_constituents(session, organization_id, current_user)
    institution_forecasts = []
    sufficient_count = 0
    insufficient_count = 0

    for inst in constituents:
        fc = await get_explainable_forecast(
            institution_id=inst.id,
            years_forward=years_forward,
            session=session,
            current_user=current_user,
        )
        if fc.status == ForecastStatus.SUFFICIENT_EVIDENCE:
            sufficient_count += 1
        else:
            insufficient_count += 1
        fc_dict = fc.model_dump(mode="json")
        fc_dict["institution_name"] = inst.name
        fc_dict["institution_type"] = inst.education_entity_type or inst.education_level or inst.entity_type
        institution_forecasts.append(fc_dict)

    return {
        "organization_id": org.id,
        "organization_name": org.name,
        "institution_count": len(constituents),
        "sufficient_evidence_forecasts": sufficient_count,
        "insufficient_evidence_forecasts": insufficient_count,
        "institution_forecasts": institution_forecasts,
    }


@org_router.get("/{organization_id}/memory")
async def get_organization_memory(
    organization_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """
    Return the organization-wide Institutional Memory rollup across all constituent institutions.
    """
    org, constituents = await _load_organization_and_constituents(session, organization_id, current_user)
    institution_memories = []
    combined_counts: Dict[str, int] = {}
    total_entries = 0

    for inst in constituents:
        mem_view = await _sync_and_build_memory_view(session, inst.id, current_user)
        mv_dict = mem_view.model_dump(mode="json")
        mv_dict["institution_name"] = inst.name
        mv_dict["institution_type"] = inst.education_entity_type or inst.education_level or inst.entity_type
        institution_memories.append(mv_dict)
        total_entries += mem_view.total_entries
        for k, v in mem_view.category_counts.items():
            combined_counts[k] = combined_counts.get(k, 0) + v

    return {
        "organization_id": org.id,
        "organization_name": org.name,
        "institution_count": len(constituents),
        "total_entries": total_entries,
        "category_counts": combined_counts,
        "institution_memories": institution_memories,
    }


@org_router.get("/{organization_id}/report")
async def get_organization_executive_report(
    organization_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """
    Return an organization-wide executive intelligence report combining the network intelligence
    summary with per-institution executive narratives.
    """
    net_report = await _build_organization_network_report(session, organization_id, current_user)
    org, constituents = await _load_organization_and_constituents(session, organization_id, current_user)
    institution_reports = []
    for inst in constituents:
        try:
            rep = await get_institution_report(
                institution_id=inst.id,
                session=session,
                current_user=current_user,
            )
            r_dict = rep.model_dump(mode="json")
            r_dict["institution_name"] = inst.name
            r_dict["institution_type"] = inst.education_entity_type or inst.education_level or inst.entity_type
            institution_reports.append(r_dict)
        except HTTPException:
            continue

    return {
        "organization_id": org.id,
        "organization_name": org.name,
        "organization_type": org.entity_category,
        "network_intelligence": net_report.model_dump(mode="json"),
        "institution_reports": institution_reports,
    }
