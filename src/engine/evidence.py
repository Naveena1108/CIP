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

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Set, Tuple
from pydantic import BaseModel, Field


def format_human_source(doc_name: Optional[str], period_str: Optional[str] = None) -> str:
    """Format technical document names into clean, readable source references."""
    if not doc_name:
        return "Institutional Records" if not period_str else f"Institutional Records · {period_str}"
    clean = doc_name
    for ext in [".xlsx", ".xls", ".csv", ".pdf", ".docx", ".doc"]:
        clean = clean.replace(ext, "")
    clean = clean.replace("_", " ").strip()
    clean = re.sub(r"\s*\(\d+\)$", "", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    if period_str and period_str not in clean:
        return f"{clean} · {period_str}"
    return clean

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

        # Also include representative provenance records from dynamic_signals (capped to prevent payload explosion)
        contradictions: List[str] = []
        if dynamic_signals:
            seen_samples: Set[Tuple[str, str]] = set()
            for d_idx, dsig in enumerate(dynamic_signals):
                coverage[dsig.domain] = True
                d_key = (dsig.domain, dsig.metric_name)
                is_sample = d_key not in seen_samples
                if is_sample or dsig.is_contradictory:
                    d_prov = self.build_provenance_from_discovered_signal(dsig, d_idx)
                    if is_sample and len(seen_samples) < 30:
                        provenance_records.append(d_prov)
                        seen_samples.add(d_key)
                    if dsig.is_contradictory:
                        if d_prov.contradiction_detail:
                            contradictions.append(d_prov.contradiction_detail)
                        if d_prov not in provenance_records and len(provenance_records) < 100:
                            provenance_records.append(d_prov)

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

        # 2. Insights from Dossier Evidence Tokens (Aggregated Longitudinal Trends & Deduplicated Findings)
        grouped_tokens: Dict[Tuple[str, str, Optional[str]], List[EvidenceToken]] = {}
        for tok in dossier.evidence_tokens:
            dept_key = None
            if tok.provenance and tok.provenance.date_or_context and "Dept:" in tok.provenance.date_or_context:
                dept_part = tok.provenance.date_or_context.split("Dept:")[1]
                dept_key = dept_part.split("(")[0].strip()
            grouped_tokens.setdefault((tok.signal_name, tok.metric_name, dept_key), []).append(tok)

        for (sig_name, metric_name, dept_val), toks in grouped_tokens.items():
            sorted_toks = sorted(toks, key=lambda t: t.academic_year or 0)
            years = [t.academic_year for t in sorted_toks if t.academic_year]
            worst_sev = "CRITICAL" if any(t.severity == "CRITICAL" for t in sorted_toks) else (
                "HIGH" if any(t.severity == "HIGH" for t in sorted_toks) else "MEDIUM"
            )
            dept_suffix = f" in {dept_val}" if dept_val else ""

            # Check if multi-year trend exists (Part 5: Finding Deduplication & Aggregation)
            if len(sorted_toks) >= 2 and len(years) >= 2:
                first_t = sorted_toks[0]
                last_t = sorted_toks[-1]
                delta_val = last_t.observed_value - first_t.observed_value
                direction = "increased" if delta_val > 0 else "decreased"
                year_range_str = f"{years[0]}–{years[-1]}"
                
                title = f"{sig_name} {direction} steadily across {len(sorted_toks)} periods{dept_suffix}"
                summary = (
                    f"Observed sustained change across AY {year_range_str}: "
                    f"shifted from {first_t.observed_value:.1f} to {last_t.observed_value:.1f} "
                    f"(latest baseline: {last_t.baseline_value:.1f}, Z-score: {last_t.deviation_zscore:+.2f})."
                )
                why_it_matters = (
                    f"A multi-year trend in {sig_name.lower()} indicates persistent structural operational drift "
                    f"rather than an isolated single-year fluctuation."
                )
                what_to_check = (
                    f"Review departmental resource allocation, student demand, and root causes for {metric_name.lower()}."
                )
                raw_doc = last_t.provenance.document if last_t.provenance else "Institutional Dataset"
                src_str = format_human_source(raw_doc, f"AY {year_range_str}")
                prov_subset = [t.provenance for t in sorted_toks if t.provenance] or all_records[:2]

                insights.append(
                    InsightWithEvidence(
                        insight_id=f"insight_trend_{sig_name}_{metric_name}_{dept_val or 'inst'}",
                        title=title,
                        category="anomaly",
                        severity_or_stage=worst_sev,
                        summary=summary,
                        department=dept_val,
                        academic_year=years[-1],
                        why_it_matters=why_it_matters,
                        what_to_check=what_to_check,
                        source=src_str,
                        technical_details={
                            "periods": years,
                            "first_value": first_t.observed_value,
                            "latest_value": last_t.observed_value,
                            "latest_baseline": last_t.baseline_value,
                            "latest_zscore": last_t.deviation_zscore,
                            "all_anomalies_count": len(sorted_toks),
                        },
                        is_uncertain=False,
                        provenance=prov_subset[:4],
                    )
                )
            else:
                tok = sorted_toks[0]
                dir_word = "dropped below baseline" if tok.observed_value < tok.baseline_value else "rose above baseline"
                title = f"{sig_name} {dir_word}{dept_suffix}"
                summary = (
                    f"In AY {tok.academic_year}, {sig_name} ({metric_name}) reached {tok.observed_value:.1f} "
                    f"compared to historical baseline {tok.baseline_value:.1f} (Z-score: {tok.deviation_zscore:+.2f})."
                )
                why_it_matters = f"Single-period shift exceeding standard historical bounds for {sig_name.lower()}."
                what_to_check = f"Verify recent operational changes or external shifts influencing {metric_name.lower()}."
                raw_doc = tok.provenance.document if tok.provenance else "Institutional Dataset"
                src_str = format_human_source(raw_doc, f"AY {tok.academic_year}")
                prov_subset = [tok.provenance] if tok.provenance else all_records[:1]

                insights.append(
                    InsightWithEvidence(
                        insight_id=f"insight_anom_{sig_name}_{metric_name}_{tok.academic_year}_{dept_val or 'inst'}",
                        title=title,
                        category="anomaly",
                        severity_or_stage=tok.severity,
                        summary=summary,
                        department=dept_val,
                        academic_year=tok.academic_year,
                        why_it_matters=why_it_matters,
                        what_to_check=what_to_check,
                        source=src_str,
                        technical_details={
                            "academic_year": tok.academic_year,
                            "observed": tok.observed_value,
                            "baseline": tok.baseline_value,
                            "zscore": tok.deviation_zscore,
                            "severity": tok.severity,
                        },
                        is_uncertain=False,
                        provenance=prov_subset,
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
                src_str = format_human_source(matching_provs[0].document if matching_provs else None)
                insights.append(
                    InsightWithEvidence(
                        insight_id=f"insight_cross_{idx}",
                        title=f"Cross-Signal: {cs.primary_domain.title()} connected with {', '.join(cs.uninspected_missing_domains[:2]) if cs.uninspected_missing_domains else 'institutional operations'}",
                        category="cross_signal",
                        severity_or_stage="Emerging Risk",
                        summary=f"{cs.trigger_summary} {cs.correlation_vs_causation_note}",
                        department=cs.department,
                        academic_year=None,
                        why_it_matters="Multi-domain co-movement provides higher-confidence risk signal than isolated departmental metrics.",
                        what_to_check="Inspect shared systemic factors linking primary domain to downstream indicators.",
                        source=src_str,
                        technical_details={
                            "primary_domain": cs.primary_domain,
                            "primary_metric": cs.primary_metric,
                            "potential_contributing_factors": cs.potential_contributing_factors,
                        },
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
                    title=f"Institutional Governance Stage: {stage_str.upper()}",
                    category="risk_progression",
                    severity_or_stage=stage_str,
                    summary=rp.stage_rationale,
                    why_it_matters="Establishes overall institutional governance risk posture under the 5-stage progression model.",
                    what_to_check="Review prioritized strategic interventions before risk drifts into higher escalation tiers.",
                    source="Institutional Intelligence Pipeline",
                    technical_details={
                        "stage": stage_str,
                        "rationale": rp.stage_rationale,
                    },
                    is_uncertain=False,
                    provenance=all_records[:3],
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
            why_matter_fc = (
                "Forecasting requires at least 2 dated historical periods; no forward speculation is fabricated."
                if is_insuff else
                "Autoregressive momentum projection reveals trajectory under status quo operations."
            )
            what_check_fc = (
                "Upload additional historical academic periods to unlock trajectory modeling."
                if is_insuff else
                "Review intervention levers to counteract projected risk momentum."
            )
            insights.append(
                InsightWithEvidence(
                    insight_id="insight_forecast",
                    title=f"Forward Risk Outlook: {forecast.status.value}",
                    category="forecast",
                    severity_or_stage=forecast.status.value,
                    summary=summary_str,
                    why_it_matters=why_matter_fc,
                    what_to_check=what_check_fc,
                    source="Autoregressive Predictor",
                    technical_details={
                        "horizon": forecast.horizon,
                        "method": forecast.method_actually_used,
                        "confidence": forecast.confidence,
                    },
                    is_uncertain=is_insuff,
                    uncertainty_reason=forecast.insufficient_evidence_reason if is_insuff else None,
                    provenance=all_records[:2],
                )
            )

        # 5. Insights for Contradictions if any
        for c_idx, c_rec in enumerate([r for r in all_records if r.is_contradictory]):
            src_str = format_human_source(c_rec.document)
            insights.append(
                InsightWithEvidence(
                    insight_id=f"insight_contradiction_{c_idx}",
                    title=f"Source Discrepancy: {c_rec.metric_name or c_rec.domain}",
                    category="contradiction_or_quality",
                    severity_or_stage="CONTRADICTION",
                    summary=c_rec.contradiction_detail or f"Conflicting source values detected in {c_rec.document}.",
                    why_it_matters="Data integrity issue: conflicting values across sources may distort historical baselines.",
                    what_to_check="Reconcile source documents and verify authoritative institutional records.",
                    source=src_str,
                    technical_details={"document": c_rec.document, "table_cell": c_rec.table_cell_or_range},
                    is_uncertain=True,
                    uncertainty_reason="Conflicting values reported across source records.",
                    provenance=[c_rec],
                )
            )

        # Quality Filter & Prioritization (Part 32: prioritize signal over volume)
        severity_order = {"CRITICAL": 0, "HIGH": 1, "Emerging Risk": 2, "MEDIUM": 3, "Anomaly": 4, "LOW": 5, "Observation": 6}
        insights.sort(key=lambda x: (severity_order.get(x.severity_or_stage, 9), 0 if x.category == "risk_progression" else 1))
        # Cap to top meaningful findings
        filtered_insights = insights[:30]

        # Canonical evidence referencing: build compact, deduplicated canonical evidence records
        canonical_provenance: List[ProvenanceRecord] = []
        seen_prov_ids: Set[str] = set()
        for ins in filtered_insights:
            for p in ins.provenance:
                if p and p.evidence_id not in seen_prov_ids:
                    seen_prov_ids.add(p.evidence_id)
                    canonical_provenance.append(p)
        for r in all_records:
            if r.evidence_id not in seen_prov_ids and len(canonical_provenance) < 60:
                seen_prov_ids.add(r.evidence_id)
                canonical_provenance.append(r)

        return EvidenceProvenanceCatalog(
            institution_id=institution_id,
            total_insights=len(filtered_insights),
            total_provenance_records=len(canonical_provenance),
            insights=filtered_insights,
            all_provenance_records=canonical_provenance,
        )
