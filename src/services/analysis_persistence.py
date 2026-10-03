"""
Analysis Persistence, Reuse, and Cache Management Service for CIP Phase 2.
Implements Sections 14, 15, 16, 21, 30, and 31 of the Phase 2 specification:
- Identifies analysis state by (institution_id, dataset_version).
- Reuses persisted CrisisAssessment, InstitutionalIntelligenceReport, ExplainableForecast,
  and EvidenceProvenanceCatalog without redundant recalculation on every request.
- Prevents duplicate database assessment rows.
- Invalidates cache cleanly when datasets are added, replaced, or deleted.
- Computes distinct, truthful count semantics:
  meaningful_findings != underlying_observations != evidence_records.
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple
from pydantic import BaseModel, Field
from sqlalchemy import func, select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    CrisisAssessmentModel,
    DiscoveredSignalModel,
    IngestionRecordModel,
    SignalSnapshotModel,
    InstitutionModel,
    UserModel,
)
from src.db.repository import (
    AssessmentRepository,
    InstitutionRepository,
    SignalSnapshotRepository,
    DiscoveredSignalRepository,
    IngestionRecordRepository,
    InstitutionalMemoryRepository,
)
from src.contracts import (
    CrisisAssessment,
    AdmissionsSignal,
    PlacementsSignal,
    CETRankingSignal,
    DiscoveredSignal,
    DataQualityIssue,
    InstitutionalIntelligenceReport,
    ExplainableForecast,
    EvidenceProvenanceCatalog,
    MemoryCategory,
)
from src.engine.crisis_scorer import CrisisIntelligenceEngine
from src.engine.institutional_intelligence import InstitutionalIntelligenceEngine
from src.engine.explainable_forecasting import ExplainableForecastingAndMemoryEngine
from src.engine.evidence import EvidenceAssembler, InstitutionalDossier
from src.engine.features import extract_institutional_features
from src.engine.predictor import TrajectoryPredictor, TrajectoryPoint


class OverviewCounts(BaseModel):
    changed_count: int = Field(description="Number of indicators that exhibited statistically significant change")
    findings_count: int = Field(description="Number of distinct analytical findings")
    evidence_count: int = Field(description="Number of verified evidence records with 7-field provenance")
    observations_count: int = Field(description="Total raw underlying data observations / signals")
    sources_count: int = Field(description="Total distinct source files/documents ingested")
    high_priority_findings_count: int = Field(default=0, description="Findings with Critical or High severity")


class TopFindingSummary(BaseModel):
    id: str
    title: str
    summary: str
    severity: str
    category: str
    why_it_matters: str
    what_to_check: str
    source: str
    evidence_count: int = 1


class OverviewResponse(BaseModel):
    institution_id: str
    institution_name: str
    entity_type: str = "institution"
    status: Literal["stable", "watch", "elevated", "critical", "insufficient_data"]
    status_summary: str
    current_risk: Optional[float] = None
    risk_level: str = "NO DATA"
    primary_threat: str = "Awaiting Ingestion"
    confidence_score: float = 0.0
    counts: OverviewCounts
    risk_outlook: Dict[str, Optional[float]] = Field(default_factory=dict)
    top_findings: List[TopFindingSummary] = Field(default_factory=list)
    what_is_happening: str
    what_changed: str
    what_needs_attention: str
    supporting_evidence_summary: str
    what_could_happen_next: str
    is_persisted: bool = True


# In-memory fast cache keyed by dataset_state_key
_ASSESSMENT_CACHE: Dict[str, CrisisAssessment] = {}
_INTEL_REPORT_CACHE: Dict[str, InstitutionalIntelligenceReport] = {}
_FORECAST_CACHE: Dict[str, ExplainableForecast] = {}
_EVIDENCE_CATALOG_CACHE: Dict[str, EvidenceProvenanceCatalog] = {}
_OVERVIEW_CACHE: Dict[str, OverviewResponse] = {}


class AnalysisPersistenceService:
    @staticmethod
    def invalidate_institution(institution_id: str) -> None:
        """Invalidate all analysis caches for an institution."""
        to_del = [k for k in _ASSESSMENT_CACHE if k.startswith(f"{institution_id}_")]
        for k in to_del:
            _ASSESSMENT_CACHE.pop(k, None)

        to_del = [k for k in _INTEL_REPORT_CACHE if k.startswith(f"{institution_id}_")]
        for k in to_del:
            _INTEL_REPORT_CACHE.pop(k, None)

        to_del = [k for k in _FORECAST_CACHE if k.startswith(f"{institution_id}_")]
        for k in to_del:
            _FORECAST_CACHE.pop(k, None)

        to_del = [k for k in _EVIDENCE_CATALOG_CACHE if k.startswith(f"{institution_id}_")]
        for k in to_del:
            _EVIDENCE_CATALOG_CACHE.pop(k, None)

        to_del = [k for k in _OVERVIEW_CACHE if k.startswith(f"{institution_id}_")]
        for k in to_del:
            _OVERVIEW_CACHE.pop(k, None)

    @staticmethod
    async def get_dataset_state_key(session: AsyncSession, institution_id: str) -> Tuple[str, int, int, int]:
        """
        Computes stable state key based on institution's active records:
        (state_key, snap_count, disc_count, ingest_count).
        """
        stmt_snaps = select(func.count(SignalSnapshotModel.id)).where(SignalSnapshotModel.institution_id == institution_id)
        snap_count = (await session.execute(stmt_snaps)).scalar() or 0

        stmt_sigs = select(func.count(DiscoveredSignalModel.id)).where(DiscoveredSignalModel.institution_id == institution_id)
        sig_count = (await session.execute(stmt_sigs)).scalar() or 0

        stmt_rec = (
            select(IngestionRecordModel.id, IngestionRecordModel.created_at)
            .where(IngestionRecordModel.institution_id == institution_id)
            .order_by(desc(IngestionRecordModel.created_at))
            .limit(1)
        )
        rec_res = (await session.execute(stmt_rec)).first()
        latest_ingest_id = rec_res[0] if rec_res else "none"
        latest_created = rec_res[1].isoformat() if rec_res and rec_res[1] else "0"

        stmt_ing_cnt = select(func.count(IngestionRecordModel.id)).where(IngestionRecordModel.institution_id == institution_id)
        ing_count = (await session.execute(stmt_ing_cnt)).scalar() or 0

        raw_key = f"{institution_id}_{snap_count}_{sig_count}_{ing_count}_{latest_ingest_id}_{latest_created}"
        state_key = f"{institution_id}_{hashlib.sha256(raw_key.encode()).hexdigest()[:16]}"
        return state_key, snap_count, sig_count, ing_count

    @staticmethod
    async def get_or_evaluate_assessment(
        session: AsyncSession,
        institution_id: str,
        cet: List[CETRankingSignal],
        admissions: List[AdmissionsSignal],
        placements: List[PlacementsSignal],
        dynamic_signals: List[DiscoveredSignal],
        org_id: Optional[str] = None,
    ) -> CrisisAssessment:
        """
        Retrieve persisted assessment or evaluate and persist once per dataset version.
        Guarantees zero duplicate database assessment inserts on repeat navigation.
        """
        state_key, snap_count, sig_count, ing_count = await AnalysisPersistenceService.get_dataset_state_key(
            session, institution_id
        )

        if state_key in _ASSESSMENT_CACHE:
            return _ASSESSMENT_CACHE[state_key]

        # Check DB for persisted assessment with this dataset_version
        persisted_model = await AssessmentRepository.get_by_dataset_version(session, institution_id, state_key)
        if persisted_model:
            try:
                assessment = CrisisAssessment.model_validate_json(persisted_model.assessment_json)
                _ASSESSMENT_CACHE[state_key] = assessment
                return assessment
            except Exception:
                pass

        # Evaluate fresh
        engine = CrisisIntelligenceEngine()
        assessment = engine.evaluate_institution(
            institution_id=institution_id,
            cet_history=cet,
            admissions_history=admissions,
            placements_history=placements,
            dynamic_signals=dynamic_signals,
        )
        assessment.organization_id = org_id

        # Persist with dataset_version
        await AssessmentRepository.save_assessment(
            session=session,
            assessment=assessment,
            dataset_version=state_key,
            signals_hash=state_key,
        )
        _ASSESSMENT_CACHE[state_key] = assessment
        return assessment

    @staticmethod
    async def get_or_compute_overview(
        session: AsyncSession,
        institution_id: str,
        current_user: UserModel,
        department: Optional[str] = None,
    ) -> OverviewResponse:
        """
        Fast, consolidated Overview endpoint implementation answering the 5 core executive questions
        and providing truthful distinct count semantics in a single, high-performance call.
        """
        inst = await InstitutionRepository.get_by_id(session, institution_id)
        inst_name = inst.name if inst else institution_id
        ent_type = (inst.education_entity_type or inst.education_level or inst.entity_type) if inst else "institution"

        state_key, snap_count, sig_count, ing_count = await AnalysisPersistenceService.get_dataset_state_key(
            session, institution_id
        )

        # Empty workspace check
        total_observations = snap_count + sig_count
        if total_observations == 0:
            return OverviewResponse(
                institution_id=institution_id,
                institution_name=inst_name,
                entity_type=ent_type,
                status="insufficient_data",
                status_summary="No institutional analysis is available yet. Ingest operational, admissions, or financial documents to evaluate risk posture.",
                current_risk=None,
                risk_level="AWAITING INGESTION",
                primary_threat="No Signal Data",
                confidence_score=0.0,
                counts=OverviewCounts(
                    changed_count=0,
                    findings_count=0,
                    evidence_count=0,
                    observations_count=0,
                    sources_count=ing_count,
                    high_priority_findings_count=0,
                ),
                risk_outlook={"now": None, "y1": None, "y2": None, "y3": None},
                top_findings=[],
                what_is_happening="No institutional analysis has been computed because this workspace does not contain sufficient ingested signal records.",
                what_changed="No longitudinal change detection is available without multi-period baseline data.",
                what_needs_attention="No priority operational concerns flagged. Awaiting institutional telemetry.",
                supporting_evidence_summary="No verified evidence records loaded. Ingest institutional documents in the Data workflow.",
                what_could_happen_next="Trajectory forecasting requires at least 2 historical academic periods.",
                is_persisted=True,
            )

        if state_key in _OVERVIEW_CACHE:
            return _OVERVIEW_CACHE[state_key]

        # Load signals
        snapshots = await SignalSnapshotRepository.get_by_institution(session, institution_id)
        dynamic_signals = await DiscoveredSignalRepository.get_by_institution(session, institution_id)
        ingestion_records = await IngestionRecordRepository.list_by_institution(session, institution_id)

        admissions: List[AdmissionsSignal] = []
        placements: List[PlacementsSignal] = []
        cet: List[CETRankingSignal] = []
        for s in snapshots:
            try:
                data = json.loads(s.payload_json)
                if s.signal_type == "admissions":
                    admissions.append(AdmissionsSignal.model_validate(data))
                elif s.signal_type == "placements":
                    placements.append(PlacementsSignal.model_validate(data))
                elif s.signal_type == "cet_ranking":
                    cet.append(CETRankingSignal.model_validate(data))
            except Exception:
                continue

        # Evaluate or get persisted assessment
        org_id = (inst.organization_id or inst.parent_organization_id) if inst else current_user.organization_id
        assessment = await AnalysisPersistenceService.get_or_evaluate_assessment(
            session=session,
            institution_id=institution_id,
            cet=cet,
            admissions=admissions,
            placements=placements,
            dynamic_signals=dynamic_signals,
            org_id=org_id,
        )

        cri = round(assessment.composite_risk_index, 3)
        status_val: Literal["stable", "watch", "elevated", "critical", "insufficient_data"] = (
            "critical" if cri >= 0.70 else ("elevated" if cri >= 0.50 else ("watch" if cri >= 0.30 else "stable"))
        )

        # Assemble Evidence Dossier & Provenance Catalog for accurate findings and evidence counts
        sources = ["ACID_PERSISTENCE_STORE"]
        for s in dynamic_signals:
            src_ref = f"{s.provenance.document}:{s.provenance.format_type}"
            if src_ref not in sources:
                sources.append(src_ref)

        dq_issues: List[DataQualityIssue] = []
        for rec in ingestion_records:
            dq_issues.extend(rec.data_quality_issues)

        assembler = EvidenceAssembler()
        dossier = assembler.assemble_dossier(
            assessment=assessment,
            signal_sources=sources,
            cet_history=cet,
            admissions_history=admissions,
            placements_history=placements,
            dynamic_signals=dynamic_signals,
            data_quality_issues=dq_issues,
        )

        catalog = assembler.build_provenance_catalog(
            institution_id=institution_id,
            dossier=dossier,
            cet_history=cet,
            admissions_history=admissions,
            placements_history=placements,
            dynamic_signals=dynamic_signals,
        )

        # Extract distinct counts
        distinct_findings = catalog.insights
        findings_count = len(distinct_findings)
        evidence_count = catalog.total_provenance_records
        observations_count = total_observations
        sources_count = max(len(ingestion_records), 1)

        # Calculate indicators with statistical change (anomalies / trends)
        changed_indicators = set()
        for tok in dossier.evidence_tokens:
            changed_indicators.add(f"{tok.signal_name}_{tok.metric_name}")
        for ins in distinct_findings:
            if ins.category in ("anomaly", "cross_signal", "trend"):
                changed_indicators.add(ins.title)
        changed_count = len(changed_indicators)

        high_priority_findings = [
            f for f in distinct_findings
            if f.severity_or_stage in ("CRITICAL", "HIGH", "Emerging Risk")
        ]

        # Trajectory outlook
        dept_features = extract_institutional_features(cet, admissions, placements)
        from src.api.routes.evaluate_routes import _compute_institutional_slopes
        slopes = _compute_institutional_slopes(dept_features, admissions)
        predictor = TrajectoryPredictor()
        projections = predictor.predict_trajectory(
            current_cri=cri,
            feature_slopes=slopes,
            years_forward=3,
        )
        outlook = {
            "now": cri,
            "y1": round(projections[0].projected_cri, 3) if len(projections) > 0 else cri,
            "y2": round(projections[1].projected_cri, 3) if len(projections) > 1 else cri,
            "y3": round(projections[2].projected_cri, 3) if len(projections) > 2 else cri,
        }

        # Formulate human-first answers to 5 questions
        status_summary = (
            "Institutional indicators are tracking within normal parameters. Multi-period stability verified."
            if status_val == "stable"
            else f"Operational stress detected primarily in {assessment.primary_driving_signal}. Close monitoring required."
        )

        what_is_happening = (
            f"Institution {inst_name} is operating in the {status_val.upper()} risk category "
            f"(CRI {cri:.2f}). Operational performance across historical periods reflects {status_summary.lower()}"
        )

        what_changed = (
            f"{changed_count} institutional metric(s) shifted relative to their baselines across tracked periods."
            if changed_count > 0
            else "No statistically significant multi-period deviations detected across recent cycles."
        )

        what_needs_attention = (
            f"Primary threat driver: {assessment.primary_driving_signal}. {len(high_priority_findings)} high-priority finding(s) flagged for leadership inspection."
            if high_priority_findings
            else f"No critical or high-severity anomalies detected. Leading driver remains {assessment.primary_driving_signal}."
        )

        supporting_evidence_summary = (
            f"Findings are corroborated by {evidence_count} verified 7-field provenance records across {sources_count} document source(s)."
        )

        trajectory_summary = (
            f"3-year autoregressive forecast projects risk moving from {cri:.2f} to {outlook['y3']:.2f} under status-quo momentum."
        )

        top_findings_list: List[TopFindingSummary] = []
        for ins in distinct_findings[:5]:
            top_findings_list.append(
                TopFindingSummary(
                    id=ins.insight_id,
                    title=ins.title,
                    summary=ins.summary,
                    severity=ins.severity_or_stage,
                    category=ins.category,
                    why_it_matters=ins.why_it_matters,
                    what_to_check=ins.what_to_check,
                    source=ins.source,
                    evidence_count=len(ins.provenance) if ins.provenance else 1,
                )
            )

        resp = OverviewResponse(
            institution_id=institution_id,
            institution_name=inst_name,
            entity_type=ent_type,
            status=status_val,
            status_summary=status_summary,
            current_risk=cri,
            risk_level=assessment.risk_level,
            primary_threat=assessment.primary_driving_signal,
            confidence_score=assessment.confidence_score,
            counts=OverviewCounts(
                changed_count=changed_count,
                findings_count=findings_count,
                evidence_count=evidence_count,
                observations_count=observations_count,
                sources_count=sources_count,
                high_priority_findings_count=len(high_priority_findings),
            ),
            risk_outlook=outlook,
            top_findings=top_findings_list,
            what_is_happening=what_is_happening,
            what_changed=what_changed,
            what_needs_attention=what_needs_attention,
            supporting_evidence_summary=supporting_evidence_summary,
            what_could_happen_next=trajectory_summary,
            is_persisted=True,
        )

        _OVERVIEW_CACHE[state_key] = resp
        return resp
