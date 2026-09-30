"""
CIP Phase 7 Engine: Organization / Network Intelligence and Cross-Institution Integration.

Supports:
- Organization / Educational Group -> multiple institutions -> departments/programs -> data/signals
- Heterogeneous institution types (school, PUC, college, engineering, medical, etc.)
- Zero hardcoded institution or organization names
- Institution View and Organization/Network View
- Strict non-causal epistemic discipline for cross-institution patterns:
  'Do not infer a shared cause merely because multiple institutions show similar changes.'
- Cross-institution comparisons where evidence and comparability permit, with explicit
  explanations for excluded non-comparable or unpopulated institutions.
"""

from collections import defaultdict
from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    OrganizationModel,
    InstitutionModel,
    HierarchyNodeModel,
    UserModel,
)
from src.db.repository import (
    OrganizationRepository,
    InstitutionRepository,
    HierarchyNodeRepository,
    SignalSnapshotRepository,
    DiscoveredSignalRepository,
    InstitutionalMemoryRepository,
    AccessPolicyRepository,
)
from src.contracts import (
    AdmissionsSignal,
    PlacementsSignal,
    CETRankingSignal,
    DiscoveredSignal,
    CrisisAssessment,
    InstitutionalIntelligenceReport,
    ExplainableForecast,
    ForecastStatus,
    EvidenceProvenanceCatalog,
    ProvenanceRecord,
    InsightWithEvidence,
    InstitutionalMemoryStoreView,
    AccessScopeLevel,
    UserAccessPermissionPolicy,
    DepartmentProgramSignalSummary,
    ConstituentInstitutionSummary,
    OrganizationInstitutionCountBreakdown,
    OrganizationCoverageSummary,
    CrossInstitutionPattern,
    CrossInstitutionComparisonEntry,
    CrossInstitutionComparison,
    OrganizationNetworkIntelligenceReport,
)
from src.engine.crisis_scorer import CrisisIntelligenceEngine
from src.engine.institutional_intelligence import InstitutionalIntelligenceEngine
from src.engine.explainable_forecasting import ExplainableForecastingAndMemoryEngine
from src.engine.evidence import EvidenceAssembler


def _normalize_institution_type_slug(inst: InstitutionModel) -> str:
    """
    Derive a normalized, domain-aware institution type slug (school, puc, engineering,
    medical, college, university, etc.) without hardcoding any institution names.
    """
    etype = (
        inst.education_entity_type_other
        if inst.education_entity_type and inst.education_entity_type.lower() == "other" and inst.education_entity_type_other
        else (inst.education_entity_type or inst.education_level or inst.entity_type or "")
    ).lower()
    dom = (inst.academic_domain or "").lower()
    if inst.academic_domains_json:
        dom = f"{dom} {inst.academic_domains_json.lower()}"
    combined = f"{etype} {dom}"

    if "school" in etype or "k-12" in combined or "k12" in combined:
        return "school"
    if "pre-university" in etype or "pre_university" in etype or "puc" in etype or "pu/" in etype or "junior college" in etype:
        return "puc"
    if "engineering" in combined or "technical" in combined or "polytechnic" in combined:
        return "engineering"
    if "med" in combined or "health" in combined or "dental" in combined or "nursing" in combined or "pharmacy" in combined:
        return "medical"
    if "university" in etype:
        return "university"
    if "college" in etype or "degree" in etype:
        return "college"
    slug = etype.replace(" ", "_").replace("-", "_").strip("_")
    return slug or "institution"


def _humanize_institution_type(inst: InstitutionModel) -> str:
    """Return a clean human-readable label for any institution type without hardcoding names."""
    slug = _normalize_institution_type_slug(inst)
    slug_labels = {
        "school": "High School / K-12",
        "puc": "Pre-University (PUC / Junior College)",
        "engineering": "Engineering & Technical Institution",
        "medical": "Medical & Health Sciences Institution",
        "college": "Degree College",
        "university": "University",
    }
    if slug in slug_labels:
        return slug_labels[slug]
    raw = (
        inst.education_entity_type_other
        if inst.education_entity_type and inst.education_entity_type.lower() == "other" and inst.education_entity_type_other
        else (inst.education_entity_type or inst.education_level or inst.entity_type or "Educational Institution")
    )
    return str(raw).replace("_", " ").strip() or "Educational Institution"


def _parse_domains(inst: InstitutionModel) -> List[str]:
    domains: List[str] = []
    if inst.academic_domains_json:
        try:
            parsed = json.loads(inst.academic_domains_json)
            if isinstance(parsed, list):
                domains = [str(d).strip() for d in parsed if str(d).strip()]
        except Exception:
            pass
    if not domains and inst.academic_domain:
        domains = [d.strip() for d in inst.academic_domain.split(",") if d.strip()]
    return domains


class OrganizationNetworkIntelligenceEngine:
    """
    Computes deterministic Organization / Network Intelligence across all constituent institutions
    of an organization or educational group.
    """

    def __init__(self) -> None:
        self.crisis_engine = CrisisIntelligenceEngine()
        self.intel_engine = InstitutionalIntelligenceEngine()
        self.forecast_engine = ExplainableForecastingAndMemoryEngine()
        self.evidence_assembler = EvidenceAssembler()

    async def evaluate_constituent_institution(
        self,
        session: AsyncSession,
        inst: InstitutionModel,
        organization_id: str,
        allowed_departments: Optional[List[str]] = None,
    ) -> Tuple[
        ConstituentInstitutionSummary,
        Optional[CrisisAssessment],
        Optional[InstitutionalIntelligenceReport],
        Optional[ExplainableForecast],
        Optional[EvidenceProvenanceCatalog],
        List[DiscoveredSignal],
    ]:
        """
        Evaluate a single constituent institution within an organization and return its summary
        along with its detailed intelligence, forecast, and provenance artifacts.
        """
        snapshots = await SignalSnapshotRepository.get_by_institution(session, inst.id)
        dynamic_signals = await DiscoveredSignalRepository.get_by_institution(session, inst.id)
        nodes = await HierarchyNodeRepository.list_by_institution(session, inst.id)
        memory_entries = await InstitutionalMemoryRepository.list_by_institution(session, inst.id)

        if allowed_departments is not None:
            allowed_set = {d.upper() for d in allowed_departments}
            snapshots = [s for s in snapshots if (s.department or "").upper() in allowed_set]
            dynamic_signals = [
                ds for ds in dynamic_signals
                if (ds.context.department or "").upper() in allowed_set
                or (ds.context.program or "").upper() in allowed_set
            ]
            nodes = [
                n for n in nodes
                if (n.code or "").upper() in allowed_set or (n.name or "").upper() in allowed_set
            ]

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
                pass

        # Build department/program hierarchy summaries (Organization -> Institution -> Department/Program -> Signals)
        dept_map: Dict[str, DepartmentProgramSignalSummary] = {}
        for n in nodes:
            if n.node_type in ("department", "program"):
                code_key = (n.code or n.name).strip().upper()
                dept_map[code_key] = DepartmentProgramSignalSummary(
                    node_code=n.code or code_key,
                    node_name=n.name,
                    node_type=n.node_type,
                )

        years_set = set()
        domains_set = set()

        for a in admissions:
            years_set.add(a.academic_year)
            domains_set.add("admissions")
            dk = (a.department or "GENERAL").strip().upper()
            if dk not in dept_map:
                dept_map[dk] = DepartmentProgramSignalSummary(node_code=dk, node_name=a.department or dk)
            dept_map[dk].signal_count += 1
            if "admissions" not in dept_map[dk].domains_present:
                dept_map[dk].domains_present.append("admissions")
            if dept_map[dk].latest_academic_year is None or a.academic_year >= dept_map[dk].latest_academic_year:
                dept_map[dk].latest_academic_year = a.academic_year
                dept_map[dk].latest_metrics["vacancy_rate"] = round(a.vacancy_rate, 4)
                dept_map[dk].latest_metrics["enrolled_count"] = float(a.enrolled_count)

        for p in placements:
            years_set.add(p.academic_year)
            domains_set.add("placements")
            dk = (p.department or "GENERAL").strip().upper()
            if dk not in dept_map:
                dept_map[dk] = DepartmentProgramSignalSummary(node_code=dk, node_name=p.department or dk)
            dept_map[dk].signal_count += 1
            if "placements" not in dept_map[dk].domains_present:
                dept_map[dk].domains_present.append("placements")
            if dept_map[dk].latest_academic_year is None or p.academic_year >= dept_map[dk].latest_academic_year:
                dept_map[dk].latest_academic_year = p.academic_year
                dept_map[dk].latest_metrics["placement_percentage"] = round(p.placement_percentage, 2)
                dept_map[dk].latest_metrics["median_salary_lpa"] = round(p.median_salary_lpa, 2)

        for c in cet:
            years_set.add(c.academic_year)
            domains_set.add("cet_ranking")
            dk = (c.department or "GENERAL").strip().upper()
            if dk not in dept_map:
                dept_map[dk] = DepartmentProgramSignalSummary(node_code=dk, node_name=c.department or dk)
            dept_map[dk].signal_count += 1
            if "cet_ranking" not in dept_map[dk].domains_present:
                dept_map[dk].domains_present.append("cet_ranking")
            if dept_map[dk].latest_academic_year is None or c.academic_year >= dept_map[dk].latest_academic_year:
                dept_map[dk].latest_academic_year = c.academic_year
                dept_map[dk].latest_metrics["closing_rank"] = float(c.closing_rank)

        for ds in dynamic_signals:
            if ds.context.academic_year:
                years_set.add(ds.context.academic_year)
            if ds.domain:
                domains_set.add(ds.domain)
            dk = (ds.context.department or ds.context.program or "INSTITUTIONAL").strip().upper()
            if dk not in dept_map:
                dept_map[dk] = DepartmentProgramSignalSummary(
                    node_code=dk,
                    node_name=ds.context.department or ds.context.program or "Institutional General",
                    node_type="program" if ds.context.program and not ds.context.department else "department",
                )
            dept_map[dk].signal_count += 1
            if ds.domain and ds.domain not in dept_map[dk].domains_present:
                dept_map[dk].domains_present.append(ds.domain)
            if ds.value is not None:
                if (
                    dept_map[dk].latest_academic_year is None
                    or (ds.context.academic_year and ds.context.academic_year >= dept_map[dk].latest_academic_year)
                ):
                    if ds.context.academic_year:
                        dept_map[dk].latest_academic_year = ds.context.academic_year
                    dept_map[dk].latest_metrics[ds.metric_name] = round(float(ds.value), 4)

        total_signals = len(snapshots) + len(dynamic_signals)
        has_data = total_signals > 0
        inst_type_slug = _normalize_institution_type_slug(inst)
        inst_type_label = _humanize_institution_type(inst)
        inst_domains = _parse_domains(inst)

        if not has_data:
            summary = ConstituentInstitutionSummary(
                institution_id=inst.id,
                name=inst.name,
                organization_id=organization_id,
                entity_category=inst.entity_category or "educational_institution",
                education_entity_type=inst.education_entity_type or inst.education_level,
                institution_type=inst_type_slug,
                institution_type_label=inst_type_label,
                academic_domains=inst_domains,
                has_data=False,
                signal_count=0,
                canonical_signal_count=0,
                dynamic_signal_count=0,
                distinct_periods=0,
                years_covered=[],
                domains_covered=[],
                departments_programs=list(dept_map.values()),
                composite_risk_index=None,
                risk_level="INSUFFICIENT_DATA",
                risk_progression_stage="insufficient_evidence",
                primary_driving_signal="No data ingested yet",
                anomaly_count=0,
                forecast_status="INSUFFICIENT_EVIDENCE",
                projected_year3_cri=None,
                provenance_record_count=0,
                memory_entry_count=len(memory_entries),
            )
            return summary, None, None, None, None, []

        assessment = self.crisis_engine.evaluate_institution(
            institution_id=inst.id,
            cet_history=cet,
            admissions_history=admissions,
            placements_history=placements,
            dynamic_signals=dynamic_signals,
        )
        assessment.organization_id = organization_id

        intel_report = self.intel_engine.analyze_institution(
            institution_id=inst.id,
            cet_history=cet,
            admissions_history=admissions,
            placements_history=placements,
            dynamic_signals=dynamic_signals,
        )
        intel_report.organization_id = organization_id

        forecast = self.forecast_engine.generate_explainable_forecast(
            institution_id=inst.id,
            cet_history=cet,
            admissions_history=admissions,
            placements_history=placements,
            dynamic_signals=dynamic_signals,
            years_forward=3,
        )
        forecast.organization_id = organization_id

        dossier = self.evidence_assembler.assemble_dossier(
            assessment=assessment,
            signal_sources=["ORGANIZATION_NETWORK_EVALUATION"],
            cet_history=cet,
            admissions_history=admissions,
            placements_history=placements,
            dynamic_signals=dynamic_signals,
        )
        dossier.organization_id = organization_id

        catalog = self.evidence_assembler.build_provenance_catalog(
            institution_id=inst.id,
            dossier=dossier,
            intel_report=intel_report,
            forecast=forecast,
            cet_history=cet,
            admissions_history=admissions,
            placements_history=placements,
            dynamic_signals=dynamic_signals,
        )
        catalog.organization_id = organization_id

        # Attribute anomaly counts to departments/programs where applicable
        for anom in intel_report.evaluated_anomalies:
            if anom.department:
                dk = anom.department.strip().upper()
                if dk in dept_map:
                    dept_map[dk].anomaly_count += 1

        rp_stage = (
            intel_report.risk_progression.overall_institutional_stage.value
            if hasattr(intel_report.risk_progression.overall_institutional_stage, "value")
            else str(intel_report.risk_progression.overall_institutional_stage)
        )
        proj_y3 = (
            forecast.prediction[-1].projected_cri
            if forecast.prediction and len(forecast.prediction) > 0
            else None
        )

        summary = ConstituentInstitutionSummary(
            institution_id=inst.id,
            name=inst.name,
            organization_id=organization_id,
            entity_category=inst.entity_category or "educational_institution",
            education_entity_type=inst.education_entity_type or inst.education_level,
            institution_type=inst_type_slug,
            institution_type_label=inst_type_label,
            academic_domains=inst_domains,
            has_data=True,
            signal_count=total_signals,
            canonical_signal_count=len(snapshots),
            dynamic_signal_count=len(dynamic_signals),
            distinct_periods=len(years_set),
            years_covered=sorted(years_set),
            domains_covered=sorted(domains_set),
            departments_programs=sorted(dept_map.values(), key=lambda x: x.node_code),
            composite_risk_index=round(assessment.composite_risk_index, 4),
            risk_level=assessment.risk_level,
            risk_progression_stage=rp_stage,
            primary_driving_signal=assessment.primary_driving_signal,
            anomaly_count=len(assessment.anomalies_detected),
            forecast_status=forecast.status.value,
            projected_year3_cri=round(proj_y3, 4) if proj_y3 is not None else None,
            provenance_record_count=catalog.total_provenance_records,
            memory_entry_count=len(memory_entries),
        )
        return summary, assessment, intel_report, forecast, catalog, dynamic_signals

    def _discover_common_emerging_patterns(
        self,
        summaries: List[ConstituentInstitutionSummary],
        intel_by_inst: Dict[str, InstitutionalIntelligenceReport],
        catalog_by_inst: Dict[str, EvidenceProvenanceCatalog],
    ) -> List[CrossInstitutionPattern]:
        """
        Identify common or emerging patterns across multiple constituent institutions.
        STRICT EPISTEMIC RULE:
        Do NOT infer a shared cause merely because multiple institutions show similar changes.
        Every pattern is labeled OBSERVED_CO_OCCURRENCE with shared_cause_inferred=False.
        """
        patterns: List[CrossInstitutionPattern] = []
        summary_map = {s.institution_id: s for s in summaries}

        # 1. Group evaluated anomalies by domain across constituent institutions
        domain_inst_anomalies: Dict[str, Dict[str, List[Any]]] = defaultdict(lambda: defaultdict(list))
        metric_inst_anomalies: Dict[Tuple[str, str], Dict[str, List[Any]]] = defaultdict(lambda: defaultdict(list))

        for inst_id, rep in intel_by_inst.items():
            for anom in rep.evaluated_anomalies:
                dom = (anom.domain or "institutional").lower().strip()
                domain_inst_anomalies[dom][inst_id].append(anom)
                metric_inst_anomalies[(dom, anom.metric_name)][inst_id].append(anom)

            # Also check worsening signals from what_changed
            if rep.what_changed and rep.what_changed.worsening_signals:
                for ws in rep.what_changed.worsening_signals:
                    dom = (ws.domain or "institutional").lower().strip()
                    if inst_id not in domain_inst_anomalies[dom]:
                        domain_inst_anomalies[dom][inst_id].append(ws)

        # 2. Check metric-level co-occurring anomalies across >= 2 institutions
        seen_domains_in_metric_patterns = set()
        for idx, ((dom, metric_name), inst_dict) in enumerate(sorted(metric_inst_anomalies.items())):
            if len(inst_dict) < 2:
                continue
            seen_domains_in_metric_patterns.add(dom)
            inst_ids = sorted(inst_dict.keys())
            inst_names = [summary_map[i].name for i in inst_ids if i in summary_map]
            inst_types = [summary_map[i].institution_type_label for i in inst_ids if i in summary_map]

            years: set[int] = set()
            prov_records: List[ProvenanceRecord] = []
            for iid in inst_ids:
                for a in inst_dict[iid]:
                    yr = getattr(a, "academic_year", None)
                    if isinstance(yr, int):
                        years.add(yr)
                cat = catalog_by_inst.get(iid)
                if cat and cat.all_provenance_records:
                    matched_p = [
                        r for r in cat.all_provenance_records
                        if (r.metric_name and r.metric_name == metric_name)
                        or (r.domain and dom in r.domain.lower())
                    ]
                    prov_records.extend((matched_p or cat.all_provenance_records)[:2])

            patterns.append(
                CrossInstitutionPattern(
                    pattern_id=f"pat_metric_{dom}_{metric_name}_{idx}",
                    domain=dom,
                    metric_name=metric_name,
                    pattern_type="CO_OCCURRING_ANOMALY",
                    direction="WORSENING",
                    institutions_involved=inst_ids,
                    institution_names_involved=inst_names,
                    institution_types_involved=inst_types,
                    academic_years=sorted(years),
                    epistemic_classification="OBSERVED_CO_OCCURRENCE",
                    shared_cause_inferred=False,
                    non_causal_explanation=(
                        f"OBSERVED CO-OCCURRENCE ONLY: {len(inst_ids)} institutions ({', '.join(inst_names)}) "
                        f"simultaneously exhibit anomalies in '{metric_name}' ({dom}). Co-occurrence across "
                        f"institutions does NOT establish a shared root cause; independent local, demographic, "
                        f"or discipline-specific dynamics must be evaluated alongside any organization-level factors."
                    ),
                    potential_independent_factors=[
                        f"Institution-specific academic or program dynamics within each entity type ({', '.join(sorted(set(inst_types)))})",
                        "Localized admissions catchment, cohort preparation, or department-level staffing variations",
                    ],
                    potential_organization_factors_to_investigate=[
                        "Verify whether any shared governance policy, central budget timing, or group-wide administrative process coincided with these periods",
                    ],
                    summary=(
                        f"Co-occurring '{metric_name}' ({dom}) anomaly observed across {len(inst_ids)} institutions: "
                        f"{', '.join(inst_names)}."
                    ),
                    supporting_evidence=prov_records[:6],
                )
            )

        # 3. Check domain-level co-occurring pressures across >= 2 institutions (even if specific metrics differ across institution types)
        for idx, (dom, inst_dict) in enumerate(sorted(domain_inst_anomalies.items())):
            if len(inst_dict) < 2 or dom in seen_domains_in_metric_patterns:
                continue
            inst_ids = sorted(inst_dict.keys())
            inst_names = [summary_map[i].name for i in inst_ids if i in summary_map]
            inst_types = [summary_map[i].institution_type_label for i in inst_ids if i in summary_map]
            metrics_seen = sorted({
                getattr(item, "metric_name", dom)
                for items in inst_dict.values()
                for item in items
            })

            prov_records = []
            for iid in inst_ids:
                cat = catalog_by_inst.get(iid)
                if cat and cat.all_provenance_records:
                    matched_p = [
                        r for r in cat.all_provenance_records
                        if r.domain and dom in r.domain.lower()
                    ]
                    prov_records.extend((matched_p or cat.all_provenance_records)[:2])

            patterns.append(
                CrossInstitutionPattern(
                    pattern_id=f"pat_domain_{dom}_{idx}",
                    domain=dom,
                    metric_name=", ".join(metrics_seen) or dom,
                    pattern_type="SHARED_DOMAIN_PRESSURE",
                    direction="WORSENING",
                    institutions_involved=inst_ids,
                    institution_names_involved=inst_names,
                    institution_types_involved=inst_types,
                    academic_years=[],
                    epistemic_classification="OBSERVED_CO_OCCURRENCE",
                    shared_cause_inferred=False,
                    non_causal_explanation=(
                        f"OBSERVED CO-OCCURRENCE ONLY: Multiple institutions ({', '.join(inst_names)}) show "
                        f"concurrent pressure in the '{dom}' domain across metrics [{', '.join(metrics_seen)}]. "
                        f"Similar domain changes across distinct institution types ({', '.join(sorted(set(inst_types)))}) "
                        f"must not be attributed to a single shared cause without direct causal evidence."
                    ),
                    potential_independent_factors=[
                        f"Distinct regulatory and academic cycles across institution types ({', '.join(sorted(set(inst_types)))})",
                        "Independent local competition, curriculum transitions, or cohort-specific shifts",
                    ],
                    potential_organization_factors_to_investigate=[
                        f"Inspect whether group-level {dom} support or shared administrative services experienced bottlenecks",
                    ],
                    summary=(
                        f"Cross-institution '{dom}' domain pressure observed in {len(inst_ids)} institutions "
                        f"({', '.join(inst_names)}) across metrics: {', '.join(metrics_seen)}."
                    ),
                    supporting_evidence=prov_records[:6],
                )
            )

        # 4. Check for divergent trajectories across the organization (e.g. some HIGH/CRITICAL, some LOW)
        populated = [s for s in summaries if s.has_data and s.composite_risk_index is not None]
        if len(populated) >= 2:
            high_crit = [s for s in populated if s.risk_level in ("HIGH", "CRITICAL")]
            low_mod = [s for s in populated if s.risk_level == "LOW"]
            if high_crit and low_mod:
                involved = [s.institution_id for s in populated]
                names = [s.name for s in populated]
                types = [s.institution_type_label for s in populated]
                prov_records = []
                for iid in involved:
                    cat = catalog_by_inst.get(iid)
                    if cat and cat.all_provenance_records:
                        prov_records.append(cat.all_provenance_records[0])

                patterns.append(
                    CrossInstitutionPattern(
                        pattern_id="pat_divergent_network_risk",
                        domain="institutional_risk",
                        metric_name="composite_risk_index",
                        pattern_type="DIVERGENT_INSTITUTIONAL_TRAJECTORY",
                        direction="MIXED",
                        institutions_involved=involved,
                        institution_names_involved=names,
                        institution_types_involved=types,
                        academic_years=[],
                        epistemic_classification="OBSERVED_CO_OCCURRENCE",
                        shared_cause_inferred=False,
                        non_causal_explanation=(
                            "OBSERVED DIVERGENCE: Constituent institutions within the same organization exhibit "
                            "divergent risk trajectories (some elevated, others stable), confirming that risk is "
                            "not uniformly driven by organization-wide membership alone."
                        ),
                        potential_independent_factors=[
                            "Institution-specific leadership, local demand, and discipline-level market conditions",
                        ],
                        potential_organization_factors_to_investigate=[
                            "Compare resource utilization and governance practices between stable and elevated-risk institutions",
                        ],
                        summary=(
                            f"Divergent risk profile across network: {len(high_crit)} institution(s) at HIGH/CRITICAL risk "
                            f"({', '.join(s.name for s in high_crit)}) while {len(low_mod)} institution(s) remain at LOW risk "
                            f"({', '.join(s.name for s in low_mod)})."
                        ),
                        supporting_evidence=prov_records[:6],
                    )
                )

        return patterns

    def _build_cross_institution_comparisons(
        self,
        summaries: List[ConstituentInstitutionSummary],
        intel_by_inst: Dict[str, InstitutionalIntelligenceReport],
        catalog_by_inst: Dict[str, EvidenceProvenanceCatalog],
        dynamic_by_inst: Dict[str, List[DiscoveredSignal]],
    ) -> List[CrossInstitutionComparison]:
        """
        Build cross-institution comparisons where evidence permits.
        Explicitly records excluded institutions (e.g., when an institution type does not report
        placement_percentage or has no ingested data for that metric).
        """
        comparisons: List[CrossInstitutionComparison] = []
        summary_map = {s.institution_id: s for s in summaries}

        # 1. Composite Risk Index comparison across all constituent institutions with data
        cri_entries: List[CrossInstitutionComparisonEntry] = []
        cri_excluded: List[Dict[str, str]] = []
        for s in summaries:
            if s.has_data and s.composite_risk_index is not None:
                cat = catalog_by_inst.get(s.institution_id)
                ev_ref = cat.all_provenance_records[0].evidence_id if (cat and cat.all_provenance_records) else None
                cri_entries.append(
                    CrossInstitutionComparisonEntry(
                        institution_id=s.institution_id,
                        institution_name=s.name,
                        education_entity_type=s.education_entity_type,
                        institution_type_label=s.institution_type_label,
                        academic_year=max(s.years_covered) if s.years_covered else None,
                        observed_value=round(s.composite_risk_index, 4),
                        baseline_value=0.30,
                        unit_or_format="index (0.0-1.0)",
                        risk_stage=s.risk_progression_stage,
                        evidence_ref=ev_ref,
                    )
                )
            else:
                cri_excluded.append({
                    "institution_id": s.institution_id,
                    "institution_name": s.name,
                    "institution_type": s.institution_type_label,
                    "reason": "Insufficient evidence: no institutional signal data ingested yet.",
                })

        if len(cri_entries) >= 2:
            vals = [e.observed_value for e in cri_entries]
            min_v, max_v = min(vals), max(vals)
            mean_v = sum(vals) / len(vals)
            comparisons.append(
                CrossInstitutionComparison(
                    comparison_id="cmp_network_cri",
                    domain="institutional_risk",
                    metric_name="composite_risk_index",
                    metric_label="Composite Risk Index (CRI)",
                    comparability_basis=(
                        "Normalized institutional risk index (0.0 to 1.0) computed against each institution's own "
                        "historical baseline and domain-applicable signals."
                    ),
                    comparable_institutions_count=len(cri_entries),
                    entries=sorted(cri_entries, key=lambda x: x.observed_value, reverse=True),
                    excluded_institutions=cri_excluded,
                    min_value=round(min_v, 4),
                    max_value=round(max_v, 4),
                    mean_value=round(mean_v, 4),
                    spread=round(max_v - min_v, 4),
                    comparison_summary=(
                        f"Across {len(cri_entries)} data-backed institutions, CRI ranges from {min_v:.3f} to {max_v:.3f} "
                        f"(network mean: {mean_v:.3f}, spread: {max_v - min_v:.3f})."
                    ),
                )
            )

        # 2. Discover shared metrics across constituent institutions (from baselines, department summaries, and dynamic signals)
        metric_observations: Dict[Tuple[str, str], Dict[str, Tuple[float, Optional[float], Optional[int], str, Optional[str]]]] = defaultdict(dict)
        # value tuple: (observed_value, baseline_value, academic_year, metric_label, evidence_ref)

        for s in summaries:
            if not s.has_data:
                continue
            iid = s.institution_id
            rep = intel_by_inst.get(iid)
            cat = catalog_by_inst.get(iid)
            default_ev = cat.all_provenance_records[0].evidence_id if (cat and cat.all_provenance_records) else None

            # From Phase 3 baselines
            if rep and rep.baselines:
                for b in rep.baselines:
                    if b.latest_value is not None:
                        dom = (b.domain or "institutional").lower().strip()
                        metric_observations[(dom, b.metric_name)][iid] = (
                            float(b.latest_value),
                            float(b.baseline_mean) if b.baseline_mean is not None else None,
                            b.latest_period or (max(s.years_covered) if s.years_covered else None),
                            b.metric_label or b.metric_name,
                            default_ev,
                        )

            # From dynamic signals (latest year per metric)
            for ds in dynamic_by_inst.get(iid, []):
                if ds.value is not None:
                    dom = (ds.domain or "institutional").lower().strip()
                    key = (dom, ds.metric_name)
                    prev = metric_observations[key].get(iid)
                    yr = ds.context.academic_year
                    if prev is None or (yr is not None and (prev[2] is None or yr >= prev[2])):
                        metric_observations[key][iid] = (
                            float(ds.value),
                            prev[1] if prev else None,
                            yr,
                            ds.metric_label or ds.metric_name,
                            f"prov_dyn_{ds.signal_id}_0",
                        )

        for (dom, metric_name), inst_vals in sorted(metric_observations.items()):
            if len(inst_vals) < 2:
                continue
            entries: List[CrossInstitutionComparisonEntry] = []
            excluded: List[Dict[str, str]] = []
            metric_label = metric_name.replace("_", " ").title()

            for s in summaries:
                if s.institution_id in inst_vals:
                    obs_v, base_v, yr, m_lbl, ev_ref = inst_vals[s.institution_id]
                    metric_label = m_lbl or metric_label
                    entries.append(
                        CrossInstitutionComparisonEntry(
                            institution_id=s.institution_id,
                            institution_name=s.name,
                            education_entity_type=s.education_entity_type,
                            institution_type_label=s.institution_type_label,
                            academic_year=yr,
                            observed_value=round(obs_v, 4),
                            baseline_value=round(base_v, 4) if base_v is not None else None,
                            unit_or_format="metric",
                            risk_stage=s.risk_progression_stage,
                            evidence_ref=ev_ref,
                        )
                    )
                else:
                    reason = (
                        "No data ingested yet for this institution."
                        if not s.has_data
                        else (
                            f"Metric '{metric_name}' ({dom}) is not reported or not applicable for "
                            f"institution type '{s.institution_type_label}'."
                        )
                    )
                    excluded.append({
                        "institution_id": s.institution_id,
                        "institution_name": s.name,
                        "institution_type": s.institution_type_label,
                        "reason": reason,
                    })

            vals = [e.observed_value for e in entries]
            min_v, max_v = min(vals), max(vals)
            mean_v = sum(vals) / len(vals)
            types_included = sorted({e.institution_type_label for e in entries})

            comparisons.append(
                CrossInstitutionComparison(
                    comparison_id=f"cmp_{dom}_{metric_name}",
                    domain=dom,
                    metric_name=metric_name,
                    metric_label=metric_label,
                    comparability_basis=(
                        f"Direct evidence comparison of '{metric_name}' ({dom}) across {len(entries)} institutions "
                        f"representing type(s): {', '.join(types_included)}. "
                        + (
                            f"{len(excluded)} institution(s) excluded where metric is inapplicable or unpopulated."
                            if excluded else "All constituent institutions included."
                        )
                    ),
                    comparable_institutions_count=len(entries),
                    entries=sorted(entries, key=lambda x: x.observed_value, reverse=True),
                    excluded_institutions=excluded,
                    min_value=round(min_v, 4),
                    max_value=round(max_v, 4),
                    mean_value=round(mean_v, 4),
                    spread=round(max_v - min_v, 4),
                    comparison_summary=(
                        f"'{metric_label}' across {len(entries)} comparable institutions ranges from "
                        f"{min_v:.2f} to {max_v:.2f} (mean: {mean_v:.2f})."
                    ),
                )
            )

        return comparisons

    async def build_organization_intelligence_report(
        self,
        session: AsyncSession,
        organization_id: str,
        current_user: UserModel,
    ) -> OrganizationNetworkIntelligenceReport:
        """
        Build the complete CIP Phase 7 Organization / Network Intelligence Report.
        """
        org = await OrganizationRepository.get_by_id(session, organization_id)
        if not org:
            # Check if an institution exists with this ID and synthesize organization metadata
            inst_as_org = await InstitutionRepository.get_by_id(session, organization_id)
            if not inst_as_org:
                raise ValueError(f"Organization '{organization_id}' not found.")
            org = OrganizationModel(
                id=inst_as_org.id,
                name=inst_as_org.name,
                entity_category=inst_as_org.entity_category or "educational_group_network",
                ownership_governance=inst_as_org.ownership_governance,
                owner_user_id=inst_as_org.owner_user_id,
            )

        policy = AccessPolicyRepository.get_policy(current_user)
        constituents = await InstitutionRepository.list_constituent_institutions(
            session=session,
            organization_id=organization_id,
            user=current_user,
        )

        summaries: List[ConstituentInstitutionSummary] = []
        intel_by_inst: Dict[str, InstitutionalIntelligenceReport] = {}
        forecast_by_inst: Dict[str, ExplainableForecast] = {}
        catalog_by_inst: Dict[str, EvidenceProvenanceCatalog] = {}
        dynamic_by_inst: Dict[str, List[DiscoveredSignal]] = {}

        for inst in constituents:
            allowed_depts = AccessPolicyRepository.get_allowed_departments_filter(current_user, inst.id)
            s, _assess, irep, fc, cat, d_sigs = await self.evaluate_constituent_institution(
                session=session,
                inst=inst,
                organization_id=organization_id,
                allowed_departments=allowed_depts,
            )
            summaries.append(s)
            if irep:
                intel_by_inst[inst.id] = irep
            if fc:
                forecast_by_inst[inst.id] = fc
            if cat:
                catalog_by_inst[inst.id] = cat
            dynamic_by_inst[inst.id] = d_sigs

        # 1. Institution count & breakdown
        total_inst = len(summaries)
        with_data = sum(1 for s in summaries if s.has_data)
        without_data = total_inst - with_data
        multi_year = sum(1 for s in summaries if s.distinct_periods >= 2)

        by_type_label: Dict[str, int] = defaultdict(int)
        by_type_slug: Dict[str, int] = defaultdict(int)
        by_risk_level: Dict[str, int] = defaultdict(int)
        by_risk_stage: Dict[str, int] = defaultdict(int)

        for s in summaries:
            by_type_label[s.institution_type_label] += 1
            by_type_slug[s.institution_type or s.institution_type_label] += 1
            by_risk_level[s.risk_level] += 1
            by_risk_stage[s.risk_progression_stage] += 1

        count_breakdown = OrganizationInstitutionCountBreakdown(
            total_institutions=total_inst,
            institutions_with_data=with_data,
            institutions_without_data=without_data,
            with_ingested_data=with_data,
            without_data=without_data,
            by_education_type=dict(by_type_label),
            by_type=dict(by_type_slug),
            by_risk_level=dict(by_risk_level),
            by_risk_stage=dict(by_risk_stage),
        )

        # 2. Data coverage summary
        all_domains = sorted({d for s in summaries for d in s.domains_covered})
        all_years = sorted({y for s in summaries for y in s.years_covered})
        all_types = sorted({s.institution_type_label for s in summaries})
        total_depts = sum(len(s.departments_programs) for s in summaries)
        total_signals = sum(s.signal_count for s in summaries)
        cov_ratio = round(with_data / total_inst, 4) if total_inst > 0 else 0.0

        per_inst_cov: Dict[str, Dict[str, Any]] = {}
        coverage_gaps: List[str] = []
        for s in summaries:
            per_inst_cov[s.institution_id] = {
                "institution_name": s.name,
                "institution_type": s.institution_type_label,
                "has_data": s.has_data,
                "signal_count": s.signal_count,
                "distinct_periods": s.distinct_periods,
                "years_covered": s.years_covered,
                "domains_covered": s.domains_covered,
                "department_program_count": len(s.departments_programs),
            }
            if not s.has_data:
                coverage_gaps.append(
                    f"Institution '{s.name}' ({s.institution_id}, {s.institution_type_label}) has 0 ingested signals."
                )
            elif s.distinct_periods < 3:
                coverage_gaps.append(
                    f"Institution '{s.name}' ({s.institution_id}) has only {s.distinct_periods} historical period(s); "
                    f"at least 3 periods are recommended for historical baseline and trajectory forecasting."
                )

        data_coverage = OrganizationCoverageSummary(
            total_institutions=total_inst,
            institutions_with_data=with_data,
            institutions_without_data=without_data,
            institutions_with_any_data=with_data,
            institutions_with_multi_year_history=multi_year,
            institution_coverage_ratio=cov_ratio,
            overall_coverage_pct=round(cov_ratio * 100.0, 2),
            institution_types_represented=all_types,
            total_departments_programs=total_depts,
            total_signals_ingested=total_signals,
            domains_covered_across_network=all_domains,
            domains_covered_across_org=all_domains,
            years_covered_across_network=all_years,
            per_institution_coverage=per_inst_cov,
            coverage_gaps=coverage_gaps,
        )

        # 3. Network-wide risk aggregation
        populated_cris = [s.composite_risk_index for s in summaries if s.composite_risk_index is not None]
        if populated_cris:
            mean_cri = round(sum(populated_cris) / len(populated_cris), 4)
            max_cri = round(max(populated_cris), 4)
            highest_inst = max(
                (s for s in summaries if s.composite_risk_index is not None),
                key=lambda x: x.composite_risk_index or 0.0,
            )
            highest_id = highest_inst.institution_id
            if max_cri >= 0.75:
                net_risk_level = "CRITICAL"
            elif max_cri >= 0.55:
                net_risk_level = "HIGH"
            elif max_cri >= 0.35:
                net_risk_level = "MEDIUM"
            else:
                net_risk_level = "LOW"
        else:
            mean_cri = None
            max_cri = None
            highest_id = None
            net_risk_level = "INSUFFICIENT_DATA"

        # 4. Common / emerging patterns (non-causal) & cross-institution comparisons
        patterns = self._discover_common_emerging_patterns(summaries, intel_by_inst, catalog_by_inst)
        comparisons = self._build_cross_institution_comparisons(
            summaries, intel_by_inst, catalog_by_inst, dynamic_by_inst
        )

        # 5. Integrated network summaries for evidence, predictions, and memory
        total_prov = sum(s.provenance_record_count for s in summaries)
        total_insights = sum(cat.total_insights for cat in catalog_by_inst.values())
        network_evidence_summary = {
            "organization_id": organization_id,
            "total_provenance_records": total_prov,
            "total_insights_across_institutions": total_insights,
            "by_institution": {
                s.institution_id: {
                    "name": s.name,
                    "provenance_record_count": s.provenance_record_count,
                    "insight_count": catalog_by_inst[s.institution_id].total_insights if s.institution_id in catalog_by_inst else 0,
                }
                for s in summaries
            },
        }

        sufficient_forecasts = sum(
            1 for s in summaries if s.forecast_status == ForecastStatus.SUFFICIENT_EVIDENCE.value
        )
        network_predictions_summary = {
            "organization_id": organization_id,
            "institutions_with_sufficient_forecast": sufficient_forecasts,
            "institutions_with_insufficient_evidence": total_inst - sufficient_forecasts,
            "by_institution": {
                s.institution_id: {
                    "name": s.name,
                    "institution_type": s.institution_type_label,
                    "forecast_status": s.forecast_status,
                    "current_cri": s.composite_risk_index,
                    "projected_year3_cri": s.projected_year3_cri,
                }
                for s in summaries
            },
        }

        network_memory_summary = {
            "organization_id": organization_id,
            "total_memory_entries": sum(s.memory_entry_count for s in summaries),
            "by_institution": {
                s.institution_id: {
                    "name": s.name,
                    "memory_entry_count": s.memory_entry_count,
                }
                for s in summaries
            },
        }

        exec_summary = (
            f"Organization '{org.name}' ({organization_id}) comprises {total_inst} constituent institution(s) "
            f"across {len(all_types)} institution type(s) ({', '.join(all_types) or 'none'}). "
            f"Data coverage spans {with_data}/{total_inst} institution(s) ({cov_ratio:.0%}) with {total_signals} total signals "
            f"across {total_depts} department(s)/program(s). "
            + (
                f"Network mean CRI is {mean_cri:.3f} (max CRI: {max_cri:.3f} at '{highest_id}', overall network level: {net_risk_level}). "
                if mean_cri is not None
                else "No constituent institution has sufficient signal data for risk scoring yet. "
            )
            + (
                f"{len(patterns)} cross-institution co-occurrence pattern(s) and {len(comparisons)} cross-institution comparison(s) "
                f"were identified under strict non-causal epistemic discipline."
            )
        )

        return OrganizationNetworkIntelligenceReport(
            organization_id=organization_id,
            organization_name=org.name,
            entity_category=org.entity_category or "educational_group_network",
            ownership_governance=org.ownership_governance,
            view_type="ORGANIZATION_NETWORK_VIEW",
            access_scope_applied=policy,
            institution_count=total_inst,
            institution_count_breakdown=count_breakdown,
            data_coverage=data_coverage,
            institution_level_risks=summaries,
            overall_network_risk_level=net_risk_level,
            mean_composite_risk_index=mean_cri,
            max_composite_risk_index=max_cri,
            highest_risk_institution_id=highest_id,
            common_emerging_patterns=patterns,
            cross_institution_comparisons=comparisons,
            network_evidence_summary=network_evidence_summary,
            network_predictions_summary=network_predictions_summary,
            network_memory_summary=network_memory_summary,
            executive_summary=exec_summary,
        )
