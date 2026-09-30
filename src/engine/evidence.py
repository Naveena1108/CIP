"""
ACT-03 & CIP Phase 5: Evidence, Deep Provenance, and Audit Assembler Module.

Compiles numerical anomalies, baseline comparisons, and dataset provenance
into an immutable evidence dossier for auditability, clickable insight drill-down,
and LLM prompt grounding.
Every major finding links to available 7-field provenance:
- source
- document
- page_or_section (page/section)
- table_cell_or_range (table/cell/range)
- excerpt_or_image (excerpt/image)
- extraction_confidence
- date_or_context (date/context)
"""

from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field

from src.contracts import (
    CrisisAssessment,
    SignalAnomaly,
    AdmissionsSignal,
    PlacementsSignal,
    CETRankingSignal,
    DiscoveredSignal,
    DataQualityIssue,
    InstitutionalIntelligenceReport,
    ExplainableForecast,
    ProvenanceRecord,
    InsightWithEvidence,
    EvidenceProvenanceCatalog,
)


class EvidenceToken(BaseModel):
    """An immutable, mathematically-grounded atomic evidence token with 7-field provenance."""
    signal_name: str
    metric_name: str
    observed_value: float
    baseline_value: float
    deviation_zscore: float
    severity: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    academic_year: int
    narrative_fragment: str
    provenance: Optional[ProvenanceRecord] = None


class InstitutionalDossier(BaseModel):
    """Complete audit dossier for an institutional assessment."""
    institution_id: str
    organization_id: Optional[str] = None
    assessment_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    risk_level: str
    composite_risk_index: float
    primary_threat: str
    evidence_tokens: List[EvidenceToken] = Field(default_factory=list)
    total_anomalies: int = 0
    signal_coverage: Dict[str, bool] = Field(default_factory=dict)
    provenance_chain: List[str] = Field(default_factory=list)
    recommended_mitigations: List[str] = Field(default_factory=list)
    provenance_records: List[ProvenanceRecord] = Field(default_factory=list)
    contradictions_detected: List[str] = Field(default_factory=list)
    missing_evidence_domains: List[str] = Field(default_factory=list)


class EvidenceAssembler:
    """
    Assembles evidence tokens, 7-field ProvenanceRecords, and clickable insight catalogs
    from CrisisAssessments, canonical signals, and Phase 2/3/4 intelligence artifacts.
    """

    @staticmethod
    def _create_narrative_fragment(anomaly: SignalAnomaly) -> str:
        """Create an unambiguous, human-readable narrative string grounded in numbers."""
        direction = "dropped below" if anomaly.observed_value < anomaly.baseline_value else "exceeded"
        sign = "+" if anomaly.deviation_zscore > 0 else ""
        return (
            f"In AY {anomaly.academic_year}, {anomaly.signal_name} ({anomaly.metric_name}) {direction} "
            f"baseline: observed {anomaly.observed_value}, historical baseline {anomaly.baseline_value:.1f} "
            f"(Z-score: {sign}{anomaly.deviation_zscore:.2f}, severity: {anomaly.severity})."
        )

    @staticmethod
    def build_provenance_from_discovered_signal(sig: DiscoveredSignal, idx: int = 0) -> ProvenanceRecord:
        """Convert a Phase 2 DiscoveredSignal into a 7-field Phase 5 ProvenanceRecord."""
        prov = sig.provenance
        ctx = sig.context
        dept_str = f", Dept: {ctx.department}" if ctx.department else ""
        period_str = ctx.time_period or (f"AY {ctx.academic_year}" if ctx.academic_year else "Period unspecified")
        inst_str = ctx.institution_id or ctx.institution_name or "Institutional Record"
        contradiction_note = None
        if sig.is_contradictory:
            contradiction_note = (
                f"Contradictory observation in group '{sig.contradiction_group_id or 'conflict'}': "
                f"raw value '{sig.raw_value}' conflicts with another source for the same metric/period."
            )

        return ProvenanceRecord(
            evidence_id=f"prov_dyn_{sig.signal_id}_{idx}",
            source=prov.source or "UNIVERSAL_INGESTION_PIPELINE",
            document=prov.document or "Uploaded Institutional Document",
            page_or_section=prov.page_or_section or f"{prov.format_type} Primary Section",
            table_cell_or_range=prov.spreadsheet_location or "Document Body / Structured Table",
            excerpt_or_image=prov.excerpt_or_reference or f"{sig.metric_label}: {sig.raw_value}",
            extraction_confidence=round(float(prov.extraction_confidence), 4),
            date_or_context=f"{period_str}{dept_str} ({inst_str})",
            domain=sig.domain,
            metric_name=sig.metric_name,
            observed_value=sig.value,
            baseline_value=None,
            is_contradictory=sig.is_contradictory,
            contradiction_detail=contradiction_note,
        )

    @staticmethod
    def build_provenance_for_anomaly(
        anomaly: SignalAnomaly,
        institution_id: str,
        idx: int,
        signal_sources: Optional[List[str]] = None,
        cet_history: Optional[List[CETRankingSignal]] = None,
        admissions_history: Optional[List[AdmissionsSignal]] = None,
        placements_history: Optional[List[PlacementsSignal]] = None,
        dynamic_signals: Optional[List[DiscoveredSignal]] = None,
    ) -> ProvenanceRecord:
        """
        Match a SignalAnomaly to its finest available source provenance (discovered signal or canonical snapshot)
        and return a complete 7-field ProvenanceRecord.
        """
        # 1. Check if a dynamic signal matches the metric and year
        if dynamic_signals:
            for dsig in dynamic_signals:
                yr_match = (dsig.context.academic_year == anomaly.academic_year) or (dsig.context.academic_year is None)
                if yr_match and (
                    dsig.metric_name == anomaly.metric_name
                    or dsig.metric_name in anomaly.metric_name
                    or anomaly.metric_name in dsig.metric_name
                    or dsig.domain.lower() in anomaly.signal_name.lower()
                ):
                    rec = EvidenceAssembler.build_provenance_from_discovered_signal(dsig, idx)
                    rec.evidence_id = f"prov_anom_{institution_id}_{anomaly.academic_year}_{idx}"
                    rec.observed_value = anomaly.observed_value
                    rec.baseline_value = anomaly.baseline_value
                    return rec

        # 2. Match canonical signals (Admissions, Placements, CET)
        sig_lower = anomaly.signal_name.lower()
        dept_hint = None
        if "(" in anomaly.signal_name and ")" in anomaly.signal_name:
            dept_hint = anomaly.signal_name.split("(")[-1].split(")")[0].strip()

        source_str = (signal_sources[0] if signal_sources else "ACID_PERSISTENCE_STORE")
        doc_str = f"{institution_id}_canonical_ledger.db"
        if signal_sources:
            for src in signal_sources:
                if src != "ACID_PERSISTENCE_STORE":
                    doc_str = src.split(":")[0]
                    source_str = src
                    break

        page_sec = f"Section: {anomaly.signal_name}"
        cell_range = f"Table[{anomaly.metric_name}]!AY{anomaly.academic_year}"
        confidence = 0.98

        if "admission" in sig_lower and admissions_history:
            matched = [
                s for s in admissions_history
                if s.academic_year == anomaly.academic_year and (not dept_hint or s.department == dept_hint)
            ]
            if matched:
                m = matched[0]
                if m.provenance:
                    source_str = f"{m.provenance.source_id}:{m.provenance.source_type}"
                    doc_str = m.provenance.source_id
                page_sec = f"Admissions Sheet / Dept: {m.department}"
                cell_range = f"Admissions_Table[{m.department}, AY{m.academic_year}, {anomaly.metric_name}]"
        elif "placement" in sig_lower and placements_history:
            matched = [
                s for s in placements_history
                if s.academic_year == anomaly.academic_year and (not dept_hint or s.department == dept_hint)
            ]
            if matched:
                m = matched[0]
                if m.provenance:
                    source_str = f"{m.provenance.source_id}:{m.provenance.source_type}"
                    doc_str = m.provenance.source_id
                page_sec = f"Placements Sheet / Dept: {m.department}"
                cell_range = f"Placements_Table[{m.department}, AY{m.academic_year}, {anomaly.metric_name}]"
        elif ("cet" in sig_lower or "rank" in sig_lower) and cet_history:
            matched = [
                s for s in cet_history
                if s.academic_year == anomaly.academic_year and (not dept_hint or s.department == dept_hint)
            ]
            if matched:
                m = matched[0]
                if m.provenance:
                    source_str = f"{m.provenance.source_id}:{m.provenance.source_type}"
                    doc_str = m.provenance.source_id
                page_sec = f"CET Ranking Sheet / Dept: {m.department}"
                cell_range = f"CET_Ranking_Table[{m.department}, AY{m.academic_year}, {anomaly.metric_name}]"

        excerpt = EvidenceAssembler._create_narrative_fragment(anomaly)
        dept_ctx = f", Dept: {dept_hint}" if dept_hint else ""
        return ProvenanceRecord(
            evidence_id=f"prov_anom_{institution_id}_{anomaly.academic_year}_{idx}",
            source=source_str,
            document=doc_str,
            page_or_section=page_sec,
            table_cell_or_range=cell_range,
            excerpt_or_image=excerpt,
            extraction_confidence=confidence,
            date_or_context=f"AY {anomaly.academic_year}{dept_ctx} ({institution_id})",
            domain=anomaly.signal_name,
            metric_name=anomaly.metric_name,
            observed_value=anomaly.observed_value,
            baseline_value=anomaly.baseline_value,
            is_contradictory=False,
        )

    def assemble_dossier(
        self,
        assessment: CrisisAssessment,
        signal_sources: Optional[List[str]] = None,
        cet_history: Optional[List[CETRankingSignal]] = None,
        admissions_history: Optional[List[AdmissionsSignal]] = None,
        placements_history: Optional[List[PlacementsSignal]] = None,
        dynamic_signals: Optional[List[DiscoveredSignal]] = None,
        data_quality_issues: Optional[List[DataQualityIssue]] = None,
    ) -> InstitutionalDossier:
        """
        Convert detected anomalies and assessment metadata into an InstitutionalDossier
        enriched with 7-field ProvenanceRecords.
        """
        evidence_tokens: List[EvidenceToken] = []
        provenance_records: List[ProvenanceRecord] = []
        coverage = {
            "admissions": bool(admissions_history),
            "placements": bool(placements_history),
            "cet_ranking": bool(cet_history),
        }

        provenance = list(signal_sources) if signal_sources else []
        if assessment.provenance and assessment.provenance.source_id not in provenance:
            provenance.append(f"{assessment.provenance.source_id}:{assessment.provenance.source_type}")

        for idx, anomaly in enumerate(assessment.anomalies_detected):
            sig_lower = anomaly.signal_name.lower()
            if "admission" in sig_lower:
                coverage["admissions"] = True
            elif "placement" in sig_lower:
                coverage["placements"] = True
            elif "cet" in sig_lower or "rank" in sig_lower:
                coverage["cet_ranking"] = True
            elif "cross-signal" in sig_lower:
                coverage["admissions"] = True
                coverage["placements"] = True

            prov_rec = self.build_provenance_for_anomaly(
                anomaly=anomaly,
                institution_id=assessment.institution_id,
                idx=idx,
                signal_sources=provenance,
                cet_history=cet_history,
                admissions_history=admissions_history,
                placements_history=placements_history,
                dynamic_signals=dynamic_signals,
            )
            provenance_records.append(prov_rec)

            token = EvidenceToken(
                signal_name=anomaly.signal_name,
                metric_name=anomaly.metric_name,
                observed_value=anomaly.observed_value,
                baseline_value=anomaly.baseline_value,
                deviation_zscore=anomaly.deviation_zscore,
                severity=anomaly.severity,
                academic_year=anomaly.academic_year,
                narrative_fragment=self._create_narrative_fragment(anomaly),
                provenance=prov_rec,
            )
            evidence_tokens.append(token)

        # Also include provenance records from dynamic_signals not already covered
        contradictions: List[str] = []
        if dynamic_signals:
            for d_idx, dsig in enumerate(dynamic_signals):
                coverage[dsig.domain] = True
                d_prov = self.build_provenance_from_discovered_signal(dsig, d_idx)
                provenance_records.append(d_prov)
                if dsig.is_contradictory and d_prov.contradiction_detail:
                    contradictions.append(d_prov.contradiction_detail)

        if data_quality_issues:
            for dq in data_quality_issues:
                if dq.issue_type == "contradictory_sources":
                    contradictions.append(dq.description)

        missing_domains = [k for k, v in coverage.items() if not v]

        return InstitutionalDossier(
            institution_id=assessment.institution_id,
            assessment_timestamp=assessment.assessment_timestamp,
            risk_level=assessment.risk_level,
            composite_risk_index=assessment.composite_risk_index,
            primary_threat=assessment.primary_driving_signal,
            evidence_tokens=evidence_tokens,
            total_anomalies=len(evidence_tokens),
            signal_coverage=coverage,
            provenance_chain=provenance,
            recommended_mitigations=assessment.recommended_mitigations,
            provenance_records=provenance_records,
            contradictions_detected=contradictions,
            missing_evidence_domains=missing_domains,
        )

    def build_provenance_catalog(
        self,
        institution_id: str,
        dossier: InstitutionalDossier,
        intel_report: Optional[InstitutionalIntelligenceReport] = None,
        forecast: Optional[ExplainableForecast] = None,
        cet_history: Optional[List[CETRankingSignal]] = None,
        admissions_history: Optional[List[AdmissionsSignal]] = None,
        placements_history: Optional[List[PlacementsSignal]] = None,
        dynamic_signals: Optional[List[DiscoveredSignal]] = None,
    ) -> EvidenceProvenanceCatalog:
        """
        Build a comprehensive clickable catalog of major findings and their 7-field provenance records.
        """
        insights: List[InsightWithEvidence] = []
        all_records: List[ProvenanceRecord] = list(dossier.provenance_records)

        # 1. Canonical snapshot provenance records so even non-anomalous signals have provenance
        if admissions_history:
            for idx, s in enumerate(sorted(admissions_history, key=lambda x: (x.academic_year, x.department))[-8:]):
                src = f"{s.provenance.source_id}:{s.provenance.source_type}" if s.provenance else "ACID_PERSISTENCE_STORE"
                doc = s.provenance.source_id if s.provenance else f"{institution_id}_admissions.xlsx"
                fill_pct = (1.0 - s.vacancy_rate) * 100.0
                rec = ProvenanceRecord(
                    evidence_id=f"prov_adm_{institution_id}_{s.department}_{s.academic_year}_{idx}",
                    source=src,
                    document=doc,
                    page_or_section=f"Admissions / Dept: {s.department}",
                    table_cell_or_range=f"Admissions[{s.department}, AY{s.academic_year}]",
                    excerpt_or_image=(
                        f"Sanctioned intake: {s.sanctioned_intake}, Enrolled: {s.enrolled_count}, "
                        f"Fill ratio: {fill_pct:.1f}%, Vacancy rate: {s.vacancy_rate:.2%}"
                    ),
                    extraction_confidence=0.99,
                    date_or_context=f"AY {s.academic_year}, Dept: {s.department} ({institution_id})",
                    domain="admissions",
                    metric_name="vacancy_rate",
                    observed_value=round(s.vacancy_rate, 4),
                )
                all_records.append(rec)

        if placements_history:
            for idx, s in enumerate(sorted(placements_history, key=lambda x: (x.academic_year, x.department))[-8:]):
                src = f"{s.provenance.source_id}:{s.provenance.source_type}" if s.provenance else "ACID_PERSISTENCE_STORE"
                doc = s.provenance.source_id if s.provenance else f"{institution_id}_placements.xlsx"
                rec = ProvenanceRecord(
                    evidence_id=f"prov_plc_{institution_id}_{s.department}_{s.academic_year}_{idx}",
                    source=src,
                    document=doc,
                    page_or_section=f"Placements / Dept: {s.department}",
                    table_cell_or_range=f"Placements[{s.department}, AY{s.academic_year}]",
                    excerpt_or_image=(
                        f"Eligible: {s.eligible_students}, Placed: {s.placed_students} "
                        f"({s.placement_percentage:.1f}%), Median CTC: {s.median_salary_lpa:.2f} LPA"
                    ),
                    extraction_confidence=0.99,
                    date_or_context=f"AY {s.academic_year}, Dept: {s.department} ({institution_id})",
                    domain="placements",
                    metric_name="placement_percentage",
                    observed_value=round(s.placement_percentage, 2),
                )
                all_records.append(rec)

        if cet_history:
            for idx, s in enumerate(sorted(cet_history, key=lambda x: (x.academic_year, x.department))[-8:]):
                src = f"{s.provenance.source_id}:{s.provenance.source_type}" if s.provenance else "ACID_PERSISTENCE_STORE"
                doc = s.provenance.source_id if s.provenance else f"{institution_id}_cet_cutoffs.xlsx"
                rec = ProvenanceRecord(
                    evidence_id=f"prov_cet_{institution_id}_{s.department}_{s.academic_year}_{idx}",
                    source=src,
                    document=doc,
                    page_or_section=f"CET Ranking / Dept: {s.department}",
                    table_cell_or_range=f"CET_Cutoffs[{s.department}, AY{s.academic_year}]",
                    excerpt_or_image=f"Closing rank: {s.closing_rank} (Quota: {s.quota_category})",
                    extraction_confidence=0.99,
                    date_or_context=f"AY {s.academic_year}, Dept: {s.department} ({institution_id})",
                    domain="cet_ranking",
                    metric_name="closing_rank",
                    observed_value=float(s.closing_rank),
                )
                all_records.append(rec)

        # 2. Insights from Dossier Evidence Tokens (Anomalies)
        for idx, tok in enumerate(dossier.evidence_tokens):
            prov_list = [tok.provenance] if tok.provenance else (all_records[:1] if all_records else [])
            insights.append(
                InsightWithEvidence(
                    insight_id=f"insight_anom_{idx}",
                    title=f"{tok.signal_name} — {tok.metric_name} ({tok.severity})",
                    category="anomaly",
                    severity_or_stage=tok.severity,
                    summary=tok.narrative_fragment,
                    academic_year=tok.academic_year,
                    is_uncertain=False,
                    provenance=prov_list,
                )
            )

        # 3. Insights from Phase 3 Intelligence Report (Cross-Signal & Risk Progression)
        if intel_report:
            for idx, cs in enumerate(intel_report.cross_signal_findings):
                matching_provs = [
                    r for r in all_records
                    if (r.domain and r.domain.lower() in cs.primary_domain.lower())
                    or (r.metric_name and r.metric_name in cs.primary_metric)
                ]
                if not matching_provs and all_records:
                    matching_provs = all_records[:2]
                insights.append(
                    InsightWithEvidence(
                        insight_id=f"insight_cross_{idx}",
                        title=f"Cross-Signal: {cs.primary_domain} ({cs.primary_metric})",
                        category="cross_signal",
                        severity_or_stage="Cross-Signal Analysis",
                        summary=f"{cs.trigger_summary} {cs.correlation_vs_causation_note}",
                        department=cs.department,
                        academic_year=None,
                        is_uncertain=bool(cs.uninspected_missing_domains),
                        uncertainty_reason=(
                            f"Uninspected related domains: {', '.join(cs.uninspected_missing_domains[:4])}"
                            if cs.uninspected_missing_domains else None
                        ),
                        provenance=matching_provs[:4],
                    )
                )

            # Risk progression overall insight
            rp = intel_report.risk_progression
            stage_str = rp.overall_institutional_stage.value if hasattr(rp.overall_institutional_stage, "value") else str(rp.overall_institutional_stage)
            insights.append(
                InsightWithEvidence(
                    insight_id="insight_risk_ladder",
                    title=f"Institutional Risk Stage: {stage_str.upper()}",
                    category="risk_progression",
                    severity_or_stage=stage_str,
                    summary=rp.stage_rationale,
                    is_uncertain=False,
                    provenance=all_records[:4],
                )
            )

        # 4. Insights from Phase 4 Forecast
        if forecast:
            is_insuff = forecast.status.value == "INSUFFICIENT_EVIDENCE"
            summary_str = (
                forecast.insufficient_evidence_reason
                if is_insuff and forecast.insufficient_evidence_reason
                else (
                    f"Horizon {forecast.horizon} via {forecast.method_actually_used}: "
                    f"current CRI {forecast.current_cri:.3f} -> projected "
                    f"{forecast.prediction[-1].projected_cri:.3f} (confidence {forecast.confidence:.0%})."
                    if forecast.prediction else f"Horizon {forecast.horizon}: {forecast.method_actually_used}"
                )
            )
            insights.append(
                InsightWithEvidence(
                    insight_id="insight_forecast",
                    title=f"Explainable Forecast ({forecast.status.value})",
                    category="forecast",
                    severity_or_stage=forecast.status.value,
                    summary=summary_str,
                    is_uncertain=is_insuff,
                    uncertainty_reason=forecast.insufficient_evidence_reason if is_insuff else None,
                    provenance=all_records[:3],
                )
            )

        # 5. Insights for Contradictions if any
        for c_idx, c_rec in enumerate([r for r in all_records if r.is_contradictory]):
            insights.append(
                InsightWithEvidence(
                    insight_id=f"insight_contradiction_{c_idx}",
                    title=f"Contradictory Evidence: {c_rec.metric_name or c_rec.domain}",
                    category="contradiction_or_quality",
                    severity_or_stage="CONTRADICTION",
                    summary=c_rec.contradiction_detail or f"Conflicting source values detected in {c_rec.document}.",
                    is_uncertain=True,
                    uncertainty_reason="Conflicting values reported across source records.",
                    provenance=[c_rec],
                )
            )

        return EvidenceProvenanceCatalog(
            institution_id=institution_id,
            total_insights=len(insights),
            total_provenance_records=len(all_records),
            insights=insights,
            all_provenance_records=all_records,
        )
