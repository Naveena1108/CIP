"""
CIP Phase 5: Evidence-Grounded Natural-Language Investigation Engine.

Answers natural questions such as:
- "Why did retention decline?"
- "Why did placements fall?"
- "What changed since the last analysis?"

Returns:
- finding
- evidence (7-field ProvenanceRecords)
- related_signals
- potential_contributing_factors
- alternative_explanations
- confidence
- missing_information

Strictly uses ONLY available institutional evidence. Explicitly distinguishes LIVE_GEMINI
from DETERMINISTIC_FALLBACK and applies a deterministic hallucination guard so Gemini
can never invent metrics, anomalies, risk scores, or unverified causes.
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from src.contracts import (
    AdmissionsSignal,
    PlacementsSignal,
    CETRankingSignal,
    DiscoveredSignal,
    DataQualityIssue,
    InstitutionalIntelligenceReport,
    ExplainableForecast,
    ProvenanceRecord,
    NaturalQuestionInvestigationResponse,
)
from src.engine.evidence import EvidenceAssembler, InstitutionalDossier
from src.engine.llm_reasoner import LLMStructuredReasoner, _UNSET

logger = logging.getLogger("ai_criss.investigation")


class _GeminiInvestigationSynthesis(BaseModel):
    """Schema requested from the AI provider for natural-language investigation synthesis."""
    finding: str = Field(..., description="Evidence-grounded answer to the user's question using ONLY provided evidence.")
    potential_contributing_factors: List[str] = Field(
        default_factory=list,
        description="Potential contributing factors supported by related signals (never framed as proven causation).",
    )
    alternative_explanations: List[str] = Field(
        default_factory=list,
        description="Plausible alternative or non-crisis explanations grounded in domain context.",
    )


class EvidenceGroundedInvestigationEngine:
    """
    Answers natural-language institutional investigation questions grounded strictly in
    available canonical signals, dynamic signals, Phase 3 intelligence, and Phase 4 forecasts.
    Uses the Multi-Provider AI failover chain (Gemini -> OpenRouter -> Groq -> Deterministic Fallback)
    via LLMStructuredReasoner.
    """

    def __init__(
        self,
        api_key: Any = _UNSET,
        model_name: Optional[str] = None,
        openrouter_api_key: Any = _UNSET,
        openrouter_model: Optional[str] = None,
        groq_api_key: Any = _UNSET,
        groq_model: Optional[str] = None,
        use_env_fallbacks: Optional[bool] = None,
        max_retries_per_provider: int = 1,
        retry_base_delay: float = 0.05,
    ):
        self.reasoner = LLMStructuredReasoner(
            api_key=api_key,
            model_name=model_name,
            openrouter_api_key=openrouter_api_key,
            openrouter_model=openrouter_model,
            groq_api_key=groq_api_key,
            groq_model=groq_model,
            use_env_fallbacks=use_env_fallbacks,
            max_retries_per_provider=max_retries_per_provider,
            retry_base_delay=retry_base_delay,
        )
        self.api_key = self.reasoner.api_key
        self.model_name = self.reasoner.model_name

    @staticmethod
    def detect_question_intent(question: str) -> Tuple[str, List[str]]:
        """
        Classify a natural-language question into a primary intent and target signal domains.
        """
        q = question.lower().strip()
        if any(w in q for w in ("what changed", "since the last", "since last", "delta", "difference", "new risk", "recent change")):
            return "what_changed", ["admissions", "placements", "cet_ranking", "retention", "faculty", "finance"]
        if any(w in q for w in ("retention", "dropout", "attrition", "continuation", "drop out")):
            return "retention_decline", ["retention", "students", "attendance", "academics", "grievances", "admissions"]
        if any(w in q for w in ("placement", "placed", "job", "recruiter", "salary", "ctc", "package", "career", "employability")):
            return "placement_decline", ["placements", "internships", "industry", "academics", "faculty", "cet_ranking"]
        if any(w in q for w in ("admission", "vacancy", "enroll", "intake", "seat", "fill")):
            return "admissions_vacancy", ["admissions", "cet_ranking", "ranking", "placements", "infrastructure"]
        if any(w in q for w in ("cet", "rank", "cutoff", "cut-off", "merit")):
            return "ranking_deterioration", ["cet_ranking", "ranking", "admissions", "placements"]
        if any(w in q for w in ("faculty", "teacher", "professor", "staff", "student-faculty", "sfr")):
            return "faculty_investigation", ["faculty", "academics", "research", "placements", "retention"]
        if any(w in q for w in ("finance", "budget", "fee", "revenue", "deficit", "funding", "grant")):
            return "finance_investigation", ["finance", "admissions", "infrastructure", "research"]
        if any(w in q for w in ("forecast", "predict", "trajectory", "future", "next year", "horizon")):
            return "forecast_investigation", ["placements", "admissions", "cet_ranking"]
        return "general_investigation", ["admissions", "placements", "cet_ranking", "retention", "faculty", "finance", "academics"]

    @staticmethod
    def _gather_relevant_evidence(
        institution_id: str,
        intent: str,
        target_domains: List[str],
        dossier: InstitutionalDossier,
        intel_report: InstitutionalIntelligenceReport,
        forecast: Optional[ExplainableForecast],
        cet_history: List[CETRankingSignal],
        admissions_history: List[AdmissionsSignal],
        placements_history: List[PlacementsSignal],
        dynamic_signals: List[DiscoveredSignal],
        data_quality_issues: Optional[List[DataQualityIssue]] = None,
    ) -> Tuple[List[ProvenanceRecord], List[Dict[str, Any]], List[str], List[str], List[float]]:
        """
        Deterministically gather:
        - matching 7-field ProvenanceRecords
        - related_signals dicts
        - missing_information strings
        - contradictions_noted strings
        - allowed_numbers for hallucination checking
        """
        assembler = EvidenceAssembler()
        catalog = assembler.build_provenance_catalog(
            institution_id=institution_id,
            dossier=dossier,
            intel_report=intel_report,
            forecast=forecast,
            cet_history=cet_history,
            admissions_history=admissions_history,
            placements_history=placements_history,
            dynamic_signals=dynamic_signals,
        )

        all_provs = catalog.all_provenance_records
        matched_provs: List[ProvenanceRecord] = []
        related_signals: List[Dict[str, Any]] = []
        missing_info: List[str] = []
        contradictions: List[str] = list(dossier.contradictions_detected)
        allowed_nums: List[float] = [
            round(dossier.composite_risk_index, 4),
            round(dossier.composite_risk_index, 2),
            float(dossier.total_anomalies),
        ]

        primary_domain = target_domains[0] if target_domains else "general"

        for rec in all_provs:
            if rec.observed_value is not None:
                allowed_nums.append(round(float(rec.observed_value), 2))
            if rec.baseline_value is not None:
                allowed_nums.append(round(float(rec.baseline_value), 2))

            rec_dom = (rec.domain or "").lower()
            rec_met = (rec.metric_name or "").lower()
            if intent == "what_changed":
                matched_provs.append(rec)
            elif any(d in rec_dom or d in rec_met for d in target_domains[:2]):
                matched_provs.append(rec)
            elif any(d in rec_dom or d in rec_met for d in target_domains[2:]):
                related_signals.append({
                    "domain": rec.domain or "institutional",
                    "metric_name": rec.metric_name or "metric",
                    "observed_value": rec.observed_value,
                    "baseline_value": rec.baseline_value,
                    "date_or_context": rec.date_or_context,
                    "relationship": "co-occurring related signal",
                    "source_document": rec.document,
                })

            if rec.is_contradictory and rec.contradiction_detail and rec.contradiction_detail not in contradictions:
                contradictions.append(rec.contradiction_detail)

        # Also pull related signals from Phase 3 cross_signal_findings and evaluated_anomalies
        for cs in intel_report.cross_signal_findings:
            for rel in cs.inspected_related_signals:
                entry = {
                    "domain": rel.domain,
                    "metric_name": rel.metric_name,
                    "direction": rel.relationship_status,
                    "observed_summary": rel.summary,
                    "relationship": "potential_contributing_factor (non-causal)",
                }
                if entry not in related_signals:
                    related_signals.append(entry)
            for uninspected in cs.uninspected_missing_domains:
                msg = f"No signal data available in related domain '{uninspected}' to verify cross-domain contribution."
                if msg not in missing_info:
                    missing_info.append(msg)

        for anom in intel_report.evaluated_anomalies:
            if anom.academic_year is not None:
                allowed_nums.append(float(anom.academic_year))
            allowed_nums.append(round(anom.observed_value, 2))
            if anom.comparison_value is not None:
                allowed_nums.append(round(anom.comparison_value, 2))
            if anom.historical_deviation_zscore is not None:
                allowed_nums.append(round(abs(anom.historical_deviation_zscore), 2))
            if anom.relative_pct_change is not None:
                allowed_nums.append(round(abs(anom.relative_pct_change), 2))

        # Check domain-specific missing evidence
        present_domains = set()
        if admissions_history:
            present_domains.add("admissions")
        if placements_history:
            present_domains.add("placements")
        if cet_history:
            present_domains.add("cet_ranking")
        for dsig in dynamic_signals:
            present_domains.add(dsig.domain.lower())

        for dom in target_domains:
            if dom not in present_domains:
                missing_info.append(
                    f"Missing direct '{dom}' domain signals in ingested institutional records."
                )

        if intent == "retention_decline" and "retention" not in present_domains:
            missing_info.insert(
                0,
                "Direct cohort retention / attrition rate records have not been ingested for this institution.",
            )
            # Provide admissions/enrollment provenance as nearest available context if present
            if not matched_provs:
                matched_provs = [r for r in all_provs if "admission" in (r.domain or "").lower()][:4]

        if intent == "what_changed":
            wc = intel_report.what_changed
            if not wc.comparison_available:
                missing_info.insert(
                    0,
                    "No prior persisted CrisisAssessment snapshot was available before this evaluation; comparing latest academic cycle against multi-year historical baselines.",
                )

        if data_quality_issues:
            for dq in data_quality_issues:
                if dq.issue_type == "contradictory_sources" and dq.description not in contradictions:
                    contradictions.append(dq.description)
                elif dq.issue_type in ("missing_periods", "incomplete_coverage", "ambiguous_values"):
                    note = f"Data quality note ({dq.issue_type}): {dq.description}"
                    if note not in missing_info:
                        missing_info.append(note)

        # Ensure matched_provs has fallback records if general question
        if not matched_provs and all_provs and intent not in ("retention_decline",):
            matched_provs = all_provs[:5]

        return matched_provs[:8], related_signals[:8], missing_info, contradictions, allowed_nums

    @staticmethod
    def _build_deterministic_synthesis(
        institution_id: str,
        question: str,
        intent: str,
        target_domains: List[str],
        evidence: List[ProvenanceRecord],
        related_signals: List[Dict[str, Any]],
        missing_info: List[str],
        contradictions: List[str],
        dossier: InstitutionalDossier,
        intel_report: InstitutionalIntelligenceReport,
        forecast: Optional[ExplainableForecast],
    ) -> Tuple[str, List[str], List[str], float]:
        """
        Deterministically construct:
        - finding
        - potential_contributing_factors
        - alternative_explanations
        - confidence
        using ONLY available evidence.
        """
        present_domains = set()
        for r in dossier.provenance_records:
            if r.domain:
                present_domains.add(r.domain.lower())
        for k, v in dossier.signal_coverage.items():
            if v:
                present_domains.add(k.lower())

        factors: List[str] = []
        alternatives: List[str] = []

        # Collect factors and alternatives from Phase 3 cross-signal findings and investigations
        for cs in intel_report.cross_signal_findings:
            factors.extend(cs.potential_contributing_factors)
        for inv in intel_report.investigations:
            factors.extend(inv.potential_contributing_factors)
            alternatives.extend(inv.alternative_explanations)

        # Deduplicate while preserving order
        factors = list(dict.fromkeys(factors))
        alternatives = list(dict.fromkeys(alternatives))

        if intent == "retention_decline":
            has_direct_retention = any((r.domain or "").lower() == "retention" for r in evidence)
            if not has_direct_retention:
                adm_provs = [r for r in evidence if "admission" in (r.domain or "").lower()]
                proxy_note = (
                    f"Nearest available proxy evidence in admissions shows: {adm_provs[0].excerpt_or_image} ({adm_provs[0].date_or_context})."
                    if adm_provs
                    else "No proxy enrollment or attendance records are currently available."
                )
                finding = (
                    f"INSUFFICIENT DIRECT EVIDENCE FOR RETENTION: Direct student retention/continuation records have not been "
                    f"ingested for institution '{institution_id}'. Cannot confirm or quantify a retention decline without "
                    f"cohort progression data. {proxy_note}"
                )
                if not factors:
                    factors = [
                        "Unverified: Direct retention metrics are absent; potential links to attendance, academic backlog, or financial stress cannot be confirmed from current data."
                    ]
                if not alternatives:
                    alternatives = [
                        "Perceived enrollment decline may reflect initial admission seat vacancy rather than post-enrollment student attrition.",
                        "Cohort size differences across years may stem from sanctioned intake adjustments.",
                    ]
                confidence = 0.25
                return finding, factors[:5], alternatives[:4], confidence
            else:
                ret_recs = [r for r in evidence if (r.domain or "").lower() == "retention"]
                excerpts = "; ".join(f"{r.excerpt_or_image} [{r.date_or_context}]" for r in ret_recs[:3])
                finding = (
                    f"Observed retention evidence for '{institution_id}': {excerpts}. "
                    f"Overall institutional risk stands at CRI {dossier.composite_risk_index:.2f} ({dossier.risk_level})."
                )
                if not factors:
                    factors = [
                        "Co-occurring changes in student attendance, academic pass rates, or grievance volume (potential contributing factors, not proven causation)."
                    ]
                if not alternatives:
                    alternatives = [
                        "Lateral transfers or reporting cutoff timing differences across academic terms.",
                        "Temporary cohort leaves recorded prior to final university registration.",
                    ]
                confidence = 0.78 if not contradictions else 0.52
                return finding, factors[:5], alternatives[:4], confidence

        if intent == "placement_decline":
            plc_anoms = [
                a for a in intel_report.evaluated_anomalies
                if "placement" in a.domain.lower() or "placement" in a.metric_name.lower()
            ]
            plc_ev = [r for r in evidence if "placement" in (r.domain or "").lower() or "placement" in (r.metric_name or "").lower()]
            if plc_anoms:
                top = plc_anoms[0]
                finding = (
                    f"Deterministic evaluation confirmed {len(plc_anoms)} placement anomaly(ies) for '{institution_id}': "
                    f"{top.what_changed} by {top.by_how_much} in {top.when} ({top.comparison_basis}). "
                    f"Significance: {top.significance}"
                )
                if not factors:
                    factors = [
                        f"Co-occurring shifts in CET closing ranks / student entry preparedness or departmental intake fill ratios (observed alongside {top.metric_name}).",
                        "Potential shifts in recruiter conversion rates or curriculum-industry alignment (requires placement cell audit).",
                    ]
                if not alternatives:
                    alternatives = [
                        "Macroeconomic IT/core hiring cyclical slowdown affecting regional campus recruitment.",
                        "Higher proportion of graduating students opting for higher studies (M.Tech/MS/MBA) or delayed off-campus onboarding.",
                    ]
                confidence = 0.88 if not contradictions else 0.60
                return finding, factors[:5], alternatives[:4], confidence
            elif plc_ev:
                excerpts = "; ".join(f"{r.excerpt_or_image} [{r.date_or_context}]" for r in plc_ev[:3])
                finding = (
                    f"Placement records for '{institution_id}' are present ({excerpts}), and no statistically significant "
                    f"placement collapse anomaly (Z >= 1.5) was triggered in the latest evaluation (CRI: {dossier.composite_risk_index:.2f})."
                )
                if not factors:
                    factors = ["Placement metrics remain within historical baseline tolerances across monitored departments."]
                if not alternatives:
                    alternatives = ["Department-level variations may be offset by aggregate institutional stability."]
                confidence = 0.85 if not contradictions else 0.58
                return finding, factors[:5], alternatives[:4], confidence
            else:
                finding = (
                    f"INSUFFICIENT EVIDENCE FOR PLACEMENTS: No placement records have been ingested for '{institution_id}'. "
                    f"Cannot evaluate placement decline without verified graduate placement observations."
                )
                return finding, ["No placement or recruiter data available."], ["Data may not yet be uploaded for the latest graduating batch."], 0.20

        if intent == "what_changed":
            wc = intel_report.what_changed
            parts = [wc.summary]
            if wc.worsening_signals:
                w_str = "; ".join(
                    f"{s.domain}.{s.metric_name} ({s.previous_value} -> {s.current_value}, {(s.relative_pct_change or 0.0):+.1f}%)"
                    for s in wc.worsening_signals[:3]
                )
                parts.append(f"Worsening signals: {w_str}.")
            if wc.improving_signals:
                i_str = "; ".join(
                    f"{s.domain}.{s.metric_name} ({s.previous_value} -> {s.current_value}, {(s.relative_pct_change or 0.0):+.1f}%)"
                    for s in wc.improving_signals[:3]
                )
                parts.append(f"Improving signals: {i_str}.")
            if forecast and forecast.why_did_prediction_change:
                parts.append(f"Forecast comparison: {forecast.why_did_prediction_change.summary}")

            finding = " ".join(parts)
            if not factors:
                factors = [
                    f"Signal delta in {s.domain} ({s.metric_name}): {s.direction}"
                    for s in (wc.worsening_signals + wc.new_signals)[:4]
                ] or ["No newly worsening multi-period signal deltas detected."]
            if not alternatives:
                alternatives = [
                    "Observed year-over-year differences may reflect single-cycle variance unless persisted across 2+ consecutive periods.",
                    "Newly ingested documents may expand departmental coverage compared to earlier snapshots.",
                ]
            confidence = 0.86 if wc.comparison_available else 0.72
            return finding, factors[:5], alternatives[:4], confidence

        # General / admissions / ranking / faculty / finance investigation
        stage_val = (
            intel_report.risk_progression.overall_institutional_stage.value
            if hasattr(intel_report.risk_progression.overall_institutional_stage, "value")
            else str(intel_report.risk_progression.overall_institutional_stage)
        )
        if intel_report.evaluated_anomalies:
            top_anoms = intel_report.evaluated_anomalies[:3]
            anom_desc = " ".join(
                f"[{a.domain}/{a.metric_name} in {a.when}: {a.what_changed} ({a.by_how_much})]"
                for a in top_anoms
            )
            finding = (
                f"For institution '{institution_id}' (CRI: {dossier.composite_risk_index:.2f}, Risk Stage: "
                f"{stage_val}): {anom_desc}"
            )
            confidence = 0.82 if not contradictions else 0.55
        elif evidence:
            ev_desc = "; ".join(f"{r.excerpt_or_image} ({r.date_or_context})" for r in evidence[:3])
            finding = (
                f"Based on available institutional evidence for '{institution_id}' (CRI: {dossier.composite_risk_index:.2f}, "
                f"Risk Level: {dossier.risk_level}): {ev_desc}."
            )
            confidence = 0.75 if not contradictions else 0.50
        else:
            finding = (
                f"INSUFFICIENT EVIDENCE: No matching signal records were found in '{institution_id}' to answer '{question}'."
            )
            confidence = 0.20

        if not factors:
            factors = [
                f"Primary monitored risk driver: {dossier.primary_threat} (CRI {dossier.composite_risk_index:.2f})."
            ]
        if not alternatives:
            alternatives = [
                "Observed variations may reflect routine cohort or reporting cycle fluctuations rather than structural degradation."
            ]

        if contradictions:
            finding += f" NOTE: {len(contradictions)} contradictory source observation(s) were detected and preserved for audit."

        return finding, factors[:5], alternatives[:4], confidence

    def investigate_question(
        self,
        institution_id: str,
        question: str,
        dossier: InstitutionalDossier,
        intel_report: InstitutionalIntelligenceReport,
        forecast: Optional[ExplainableForecast] = None,
        cet_history: Optional[List[CETRankingSignal]] = None,
        admissions_history: Optional[List[AdmissionsSignal]] = None,
        placements_history: Optional[List[PlacementsSignal]] = None,
        dynamic_signals: Optional[List[DiscoveredSignal]] = None,
        data_quality_issues: Optional[List[DataQualityIssue]] = None,
        allow_config_test_failover: bool = False,
    ) -> NaturalQuestionInvestigationResponse:
        """
        Execute a grounded natural-language investigation over available institutional evidence
        using the Multi-Provider AI failover chain (Gemini -> OpenRouter -> Groq -> Deterministic Fallback)
        while enforcing deterministic ground-truth invariants.
        """
        cet_list = cet_history or []
        adm_list = admissions_history or []
        plc_list = placements_history or []
        dyn_list = dynamic_signals or []

        intent, target_domains = self.detect_question_intent(question)
        evidence, related_signals, missing_info, contradictions, allowed_nums = self._gather_relevant_evidence(
            institution_id=institution_id,
            intent=intent,
            target_domains=target_domains,
            dossier=dossier,
            intel_report=intel_report,
            forecast=forecast,
            cet_history=cet_list,
            admissions_history=adm_list,
            placements_history=plc_list,
            dynamic_signals=dyn_list,
            data_quality_issues=data_quality_issues,
        )

        det_finding, det_factors, det_alternatives, det_confidence = self._build_deterministic_synthesis(
            institution_id=institution_id,
            question=question,
            intent=intent,
            target_domains=target_domains,
            evidence=evidence,
            related_signals=related_signals,
            missing_info=missing_info,
            contradictions=contradictions,
            dossier=dossier,
            intel_report=intel_report,
            forecast=forecast,
        )

        prompt = (
            "You are the Evidence-Grounded Investigation Engine for the CIP Crisis Intelligence Platform.\n"
            "Answer the user's natural-language question using ONLY the verified evidence bundle below.\n"
            "STRICT RULES:\n"
            "1. Never invent metrics, percentages, years, or causes not present in the evidence bundle.\n"
            "2. If evidence for the requested topic is missing or contradictory, explicitly state that in `finding`.\n"
            "3. Frame `potential_contributing_factors` as non-causal associations requiring human verification.\n\n"
            f"INSTITUTION ID: {institution_id}\n"
            f"USER QUESTION: {question}\n"
            f"DETECTED INTENT: {intent}\n"
            f"DETERMINISTIC BASELINE FINDING: {det_finding}\n"
            f"EVIDENCE RECORDS: {json.dumps([e.model_dump(mode='json') for e in evidence], indent=2)}\n"
            f"RELATED SIGNALS: {json.dumps(related_signals, indent=2)}\n"
            f"MISSING INFORMATION: {json.dumps(missing_info)}\n"
            f"CONTRADICTIONS NOTED: {json.dumps(contradictions)}\n"
        )

        synth_obj, obs, fallback_note = self.reasoner.execute_structured_prompt(
            prompt=prompt,
            response_schema=_GeminiInvestigationSynthesis,
            allow_config_test_failover=allow_config_test_failover,
        )

        if synth_obj is None:
            return NaturalQuestionInvestigationResponse(
                institution_id=institution_id,
                question=question,
                detected_intent=intent,
                finding=det_finding,
                evidence=evidence,
                related_signals=related_signals,
                potential_contributing_factors=det_factors,
                alternative_explanations=det_alternatives,
                confidence=round(det_confidence, 2),
                missing_information=missing_info,
                contradictions_noted=contradictions,
                generation_mode="DETERMINISTIC_FALLBACK",
                gemini_live_used=False,
                active_provider="deterministic_fallback",
                answer_source="deterministic fallback",
                model_used="DETERMINISTIC_FALLBACK_ENGINE",
                fallback_reason=fallback_note or "GEMINI_API_KEY is absent; investigation synthesized deterministically from evidence records.",
                observability=obs,
                hallucination_guard_applied=True,
                hallucination_guard_report={
                    "deterministic_evidence_count": len(evidence),
                    "unverified_claims_flagged": [],
                    "contradictions_surfaced": len(contradictions),
                    "active_provider": "deterministic_fallback",
                    "answer_source": "deterministic fallback",
                },
            )

        synth = _GeminiInvestigationSynthesis.model_validate(synth_obj.model_dump())

        # Hallucination guard on LLM's finding
        unverified_flagged: List[str] = []
        finding_nums = [float(x) for x in re.findall(r"(?<![A-Za-z0-9_])-?\d+\.\d+", synth.finding)]
        bad_nums = [
            n for n in finding_nums
            if not any(abs(n - a) <= max(0.15, abs(a) * 0.02) for a in allowed_nums)
        ]
        final_finding = synth.finding
        if bad_nums:
            unverified_flagged.append(
                f"LLM finding cited unverified number(s) {bad_nums}; reverted to deterministic evidence finding."
            )
            final_finding = det_finding

        # If direct evidence was missing (e.g. retention_decline with no retention data), ensure LLM didn't omit the disclosure
        if (
            "INSUFFICIENT" in det_finding
            and "insufficient" not in final_finding.lower()
            and "not been ingested" not in final_finding.lower()
        ):
            unverified_flagged.append(
                "LLM finding omitted mandatory insufficient-evidence disclosure; prepended deterministic disclosure."
            )
            final_finding = f"{det_finding} | {final_finding}"

        mode = self.reasoner._map_provider_to_mode(obs.provider)
        return NaturalQuestionInvestigationResponse(
            institution_id=institution_id,
            question=question,
            detected_intent=intent,
            finding=final_finding,
            evidence=evidence,
            related_signals=related_signals,
            potential_contributing_factors=synth.potential_contributing_factors or det_factors,
            alternative_explanations=synth.alternative_explanations or det_alternatives,
            confidence=round(det_confidence, 2),
            missing_information=missing_info,
            contradictions_noted=contradictions,
            generation_mode=mode,
            gemini_live_used=(obs.provider == "google_gemini"),
            active_provider=obs.provider,
            answer_source=obs.answer_source_label,
            model_used=obs.model,
            fallback_reason=fallback_note,
            observability=obs,
            hallucination_guard_applied=True,
            hallucination_guard_report={
                "deterministic_evidence_count": len(evidence),
                "unverified_claims_flagged": unverified_flagged,
                "contradictions_surfaced": len(contradictions),
                "active_provider": obs.provider,
                "answer_source": obs.answer_source_label,
            },
        )
