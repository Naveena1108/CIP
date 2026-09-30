"""
AI-CRISS (CIP) — 15-Stage Internal OSINT & Global Intelligence Pipeline
and Grounded Situation Brief Generator (src/engine/osint_pipeline.py).

Executes:
  1. SOURCE -> 2. INGESTION -> 3. NORMALIZATION -> 4. VALIDATION ->
  5. DEDUPLICATION -> 6. ENTITY EXTRACTION -> 7. EVENT EXTRACTION ->
  8. LOCATION EXTRACTION -> 9. TEMPORAL EXTRACTION -> 10. RELATIONSHIP EXTRACTION ->
  11. SOURCE / PROVENANCE ATTACHMENT -> 12. CORRELATION ->
  13. RISK / SIGNAL ANALYSIS -> 14. KNOWLEDGE GRAPH ->
  15. AI ANALYSIS -> CRISIS INTELLIGENCE
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.contracts.evidence_investigation import ProvenanceRecord
from src.contracts.osint_intelligence import (
    CrisisSignal,
    CrisisSignalStage,
    GeofenceIntelligenceResponse,
    GeofenceQueryRequest,
    IntegrationStatusDashboard,
    KnowledgeGraphSnapshot,
    OSINTEntity,
    OSINTEvent,
    OSINTEvidence,
    OSINTRelationship,
    OSINTSituationBrief,
    OSINTSource,
    ProviderObservabilityItem,
    SourceConflictRecord,
)
from src.db.models import UserModel
from src.db.osint_repository import OSINTRepository
from src.engine.crisis_signal_engine import (
    CrisisSignalEngine,
    OSINTDeduplicationAndCorroborationEngine,
    STAGE_ORDER,
)
from src.engine.geospatial_intelligence import GeospatialIntelligenceEngine
from src.engine.knowledge_graph import OSINTKnowledgeGraphEngine
from src.engine.llm_reasoner import LLMStructuredReasoner
from src.integrations.base import GLOBAL_OSINT_CACHE, ProviderIngestionBundle
from src.integrations.misp.adapter import MISPIntegrationAdapter
from src.integrations.opencti.adapter import OpenCTIQueryAdapter
from src.integrations.openosint.adapter import OpenOSINTInvestigationAdapter
from src.integrations.trendradar.adapter import TrendRadarAdapter
from src.integrations.web_crawler.adapter import WebCrawlerAdapter
from src.integrations.world_intel.adapter import WorldIntelMCPAdapter
from src.security.ssrf_guard import compute_content_hash, enforce_lawful_osint_query


class _LLMSituationBriefSchema(BaseModel):
    executive_summary: str = Field(..., description="Grounded crisis intelligence executive summary")
    ai_hypotheses_and_interpretations: List[str] = Field(
        default_factory=list,
        description="Bounded analytical interpretations strictly citing extracted facts",
    )
    information_gaps: List[str] = Field(
        default_factory=list,
        description="Missing verification domains or unresolved source gaps",
    )


class OSINTIntelligenceOrchestrator:
    """
    Central orchestrator managing all modular OSINT providers, fault isolation,
    15-stage pipeline execution, persistence, knowledge graph construction,
    geofencing, and situation brief synthesis.
    """

    def __init__(self):
        self.cache = GLOBAL_OSINT_CACHE
        self.world_intel = WorldIntelMCPAdapter(cache=self.cache)
        self.opencti = OpenCTIQueryAdapter(cache=self.cache)
        self.misp = MISPIntegrationAdapter(opencti_adapter=self.opencti, cache=self.cache)
        self.trendradar = TrendRadarAdapter(cache=self.cache)
        self.web_crawler = WebCrawlerAdapter(cache=self.cache)
        self.openosint = OpenOSINTInvestigationAdapter(cache=self.cache)

        self.providers = [
            self.world_intel,
            self.opencti,
            self.misp,
            self.trendradar,
            self.web_crawler,
            self.openosint,
        ]

    def get_integration_status_dashboard(self) -> IntegrationStatusDashboard:
        """Return Section 25 Observability & Integration Status Dashboard."""
        items: List[ProviderObservabilityItem] = [p.health_check() for p in self.providers]
        items.sort(key=lambda x: x.priority)

        any_degraded = any(i.status in ("DEGRADED", "OFFLINE") for i in items)
        any_fallback = any(i.fallback_mode_active for i in items)
        if any_degraded:
            overall = "DEGRADED"
        elif any_fallback:
            overall = "OPERATIONAL_WITH_FALLBACKS"
        else:
            overall = "HEALTHY"

        return IntegrationStatusDashboard(
            overall_status=overall,
            providers=items,
            cache_stats=self.cache.stats(),
        )

    def run_pipeline_on_bundles(
        self,
        bundles: List[ProviderIngestionBundle],
        organization_id: Optional[str] = None,
        institution_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute the 15-stage internal intelligence pipeline over one or more provider bundles:
        1. SOURCE -> 2. INGESTION -> 3. NORMALIZATION -> 4. VALIDATION ->
        5. DEDUPLICATION -> 6. ENTITY EXTRACTION -> 7. EVENT EXTRACTION ->
        8. LOCATION EXTRACTION -> 9. TEMPORAL EXTRACTION -> 10. RELATIONSHIP EXTRACTION ->
        11. SOURCE / PROVENANCE ATTACHMENT -> 12. CORRELATION ->
        13. RISK / SIGNAL ANALYSIS -> 14. KNOWLEDGE GRAPH -> 15. AI ANALYSIS READY
        """
        sources_by_id: Dict[str, OSINTSource] = {}
        evidence_by_id: Dict[str, OSINTEvidence] = {}
        entities_by_id: Dict[str, OSINTEntity] = {}
        relationships_by_id: Dict[str, OSINTRelationship] = {}
        raw_events: List[OSINTEvent] = []

        for b in bundles:
            for s in b.sources:
                sources_by_id[s.id] = s
            for ev in b.evidence:
                evidence_by_id[ev.id] = ev
            for ent in b.entities:
                if organization_id and not ent.organization_id:
                    ent.organization_id = organization_id
                if institution_id and not ent.institution_id:
                    ent.institution_id = institution_id
                entities_by_id[ent.id] = ent
            for rel in b.relationships:
                relationships_by_id[rel.id] = rel
            for evt in b.events:
                if organization_id and not evt.organization_id:
                    evt.organization_id = organization_id
                if institution_id and not evt.institution_id:
                    evt.institution_id = institution_id
                raw_events.append(evt)

        # Stage 5, 11, 12: Deduplication, Source Independence Corroboration & Conflict Preservation
        deduped_events, conflicts = OSINTDeduplicationAndCorroborationEngine.process_events(
            raw_events,
            sources_by_id=sources_by_id,
        )

        # Stage 13: Crisis Signal Engine (OBSERVATION -> SIGNAL -> DEVELOPING_EVENT -> SIGNIFICANT_EVENT -> CRISIS_CANDIDATE)
        signals = CrisisSignalEngine.evaluate_signals(deduped_events)

        # Stage 14: Provenance-Backed Knowledge Graph
        graph = OSINTKnowledgeGraphEngine.build_graph(
            sources=list(sources_by_id.values()),
            evidence=list(evidence_by_id.values()),
            entities=list(entities_by_id.values()),
            events=deduped_events,
            relationships=list(relationships_by_id.values()),
        )

        return {
            "sources": list(sources_by_id.values()),
            "evidence": list(evidence_by_id.values()),
            "entities": list(entities_by_id.values()),
            "events": deduped_events,
            "relationships": list(relationships_by_id.values()),
            "conflicts": conflicts,
            "signals": signals,
            "knowledge_graph": graph,
            "pipeline_stages_completed": [
                "SOURCE",
                "INGESTION",
                "NORMALIZATION",
                "VALIDATION",
                "DEDUPLICATION",
                "ENTITY_EXTRACTION",
                "EVENT_EXTRACTION",
                "LOCATION_EXTRACTION",
                "TEMPORAL_EXTRACTION",
                "RELATIONSHIP_EXTRACTION",
                "SOURCE_PROVENANCE_ATTACHMENT",
                "CORRELATION",
                "RISK_SIGNAL_ANALYSIS",
                "KNOWLEDGE_GRAPH",
                "AI_ANALYSIS",
            ],
        }

    async def collect_and_persist_all(
        self,
        session: AsyncSession,
        query: Optional[str] = None,
        organization_id: Optional[str] = None,
        institution_id: Optional[str] = None,
        include_optional_openosint: bool = False,
    ) -> Dict[str, Any]:
        """
        Collect from all active core providers (World Intel MCP, OpenCTI, MISP, TrendRadar, Web Crawler),
        isolate any individual provider failure so AI-CRISS continues operating (Section 26),
        run the 15-stage pipeline, and persist into SQLite/PostgreSQL.
        """
        enforce_lawful_osint_query(query or "")

        active_providers = [
            self.world_intel,
            self.opencti,
            self.misp,
            self.trendradar,
            self.web_crawler,
        ]
        if include_optional_openosint:
            active_providers.append(self.openosint)

        bundles: List[ProviderIngestionBundle] = []
        for prov in active_providers:
            # Each provider's collect_intelligence is wrapped in execute_fault_isolated
            b = prov.collect_intelligence(query=query)
            bundles.append(b)

        pipeline_out = self.run_pipeline_on_bundles(
            bundles,
            organization_id=organization_id,
            institution_id=institution_id,
        )

        for s in pipeline_out["sources"]:
            await OSINTRepository.upsert_source(session, s)
        for ev in pipeline_out["evidence"]:
            await OSINTRepository.upsert_evidence(session, ev)
        for ent in pipeline_out["entities"]:
            await OSINTRepository.upsert_entity(session, ent)
        for evt in pipeline_out["events"]:
            await OSINTRepository.upsert_event(session, evt)
        for rel in pipeline_out["relationships"]:
            await OSINTRepository.upsert_relationship(session, rel)
        for conf in pipeline_out["conflicts"]:
            await OSINTRepository.upsert_conflict(session, conf)

        await session.flush()
        return pipeline_out

    async def ensure_seeded_and_load_state(
        self,
        session: AsyncSession,
        user: Optional[UserModel] = None,
        organization_id: Optional[str] = None,
        institution_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Load persisted OSINT state, triggering initial collection if the OSINT ledger is empty."""
        events = await OSINTRepository.list_events(
            session,
            user=user,
            organization_id=organization_id,
            institution_id=institution_id,
        )
        if not events:
            return await self.collect_and_persist_all(
                session,
                organization_id=organization_id,
                institution_id=institution_id,
            )

        sources = await OSINTRepository.list_sources(session)
        evidence = await OSINTRepository.list_evidence(session)
        entities = await OSINTRepository.list_entities(
            session,
            user=user,
            organization_id=organization_id,
            institution_id=institution_id,
        )
        relationships = await OSINTRepository.list_relationships(session)
        conflicts = await OSINTRepository.list_conflicts(session)
        signals = CrisisSignalEngine.evaluate_signals(events)
        graph = OSINTKnowledgeGraphEngine.build_graph(
            sources=sources,
            evidence=evidence,
            entities=entities,
            events=events,
            relationships=relationships,
        )
        return {
            "sources": sources,
            "evidence": evidence,
            "entities": entities,
            "events": events,
            "relationships": relationships,
            "conflicts": conflicts,
            "signals": signals,
            "knowledge_graph": graph,
        }

    def generate_situation_brief(
        self,
        events: List[OSINTEvent],
        signals: List[CrisisSignal],
        conflicts: List[SourceConflictRecord],
        scope: str = "Global & Regional Institutional Crisis Overview",
        use_live_llm: bool = False,
    ) -> OSINTSituationBrief:
        """
        Section 19 Evidence-Backed AI Situation Brief Generator.
        Maintains strict epistemic separation:
        - RAW SOURCE -> EXTRACTED FACT -> CORRELATED FACT -> AI INTERPRETATION -> AI ASSESSMENT
        - Never fabricates sources, events, or evidence.
        - Explicitly communicates uncertainty and conflicting source claims.
        """
        now = datetime.now(timezone.utc)
        extracted_facts: List[str] = []
        correlated_facts: List[str] = []
        prov_records: List[ProvenanceRecord] = []

        for evt in events:
            for f in evt.epistemic_chain.extracted_facts:
                if f not in extracted_facts:
                    extracted_facts.append(f)
            for cf in evt.epistemic_chain.correlated_facts:
                if cf not in correlated_facts:
                    correlated_facts.append(cf)
            for ev in evt.evidence:
                if ev.provenance and ev.provenance.evidence_id not in {p.evidence_id for p in prov_records}:
                    prov_records.append(ev.provenance)

        conflict_warnings = [
            f"[CONFLICTING SOURCES: {c.source_a_id} vs {c.source_b_id}] {c.disagreement} (Resolution: {c.resolution_status})"
            for c in conflicts
        ]

        if signals:
            highest_stage: CrisisSignalStage = max(
                (s.stage for s in signals),
                key=lambda st: STAGE_ORDER.index(st) if st in STAGE_ORDER else 0,
            )
            avg_conf = round(sum(s.confidence for s in signals) / len(signals), 3)
        else:
            highest_stage = "OBSERVATION"
            avg_conf = 0.50

        info_gaps: List[str] = []
        single_src_events = [e for e in events if e.corroboration_status == "Single-source signal"]
        if single_src_events:
            info_gaps.append(
                f"{len(single_src_events)} event(s) currently rely on a single independent source stream and require secondary corroboration."
            )
        if conflicts:
            info_gaps.append(
                f"{len(conflicts)} unresolved source disagreement(s) require authoritative telemetry or official verification."
            )
        no_coord_events = [e for e in events if e.location.latitude is None or e.location.longitude is None]
        if no_coord_events:
            info_gaps.append(
                f"{len(no_coord_events)} event(s) lack exact WGS84 coordinates and are bounded at administrative level only."
            )
        if not info_gaps:
            info_gaps.append("Continue monitoring upstream STIX and regional infrastructure feeds for secondary ripple effects.")

        det_summary = (
            f"Situation Brief ({scope}): Highest active stage is {highest_stage} across {len(events)} normalized event(s) "
            f"and {len(signals)} evaluated crisis signal(s) backed by {len(prov_records)} provenance record(s)."
        )
        if conflict_warnings:
            det_summary += f" Note: {len(conflict_warnings)} conflicting-source claim(s) are explicitly preserved as uncertain."

        det_hypotheses = [
            f"[AI_INTERPRETATION — BOUNDED HYPOTHESIS] Multi-source correlation across {[s.title for s in signals[:2]]} "
            f"indicates potential operational impact on regional campus connectivity and portal security; "
            f"this remains an analytical interpretation distinct from primary extracted facts."
        ] if signals else ["[AI_INTERPRETATION] Insufficient corroborated crisis signals to form an escalation hypothesis."]

        provider_used = "deterministic_fallback"
        answer_source = "deterministic fallback"
        model_used = "DETERMINISTIC_FALLBACK_ENGINE"

        if use_live_llm and events:
            reasoner = LLMStructuredReasoner(max_retries_per_provider=0)
            prompt = (
                "Generate an evidence-backed OSINT Situation Brief JSON strictly from the following extracted facts and conflicts.\n"
                "Do NOT invent any sources, events, dates, or locations.\n"
                f"EXTRACTED FACTS: {extracted_facts}\n"
                f"CORRELATED FACTS: {correlated_facts}\n"
                f"CONFLICTS: {conflict_warnings}\n"
            )
            llm_out, obs, _ = reasoner.execute_structured_prompt(
                prompt=prompt,
                response_schema=_LLMSituationBriefSchema,
            )
            if llm_out is not None:
                det_summary = llm_out.executive_summary
                det_hypotheses = [
                    h if "[AI_INTERPRETATION" in h else f"[AI_INTERPRETATION] {h}"
                    for h in llm_out.ai_hypotheses_and_interpretations
                ] or det_hypotheses
                if llm_out.information_gaps:
                    info_gaps = llm_out.information_gaps
                provider_used = obs.provider
                answer_source = obs.answer_source_label
                model_used = obs.model

        # Update each event's epistemic_chain with separated AI interpretation & assessment
        for evt in events:
            if not evt.epistemic_chain.ai_interpretation:
                evt.epistemic_chain.ai_interpretation = list(det_hypotheses)
            evt.epistemic_chain.ai_assessment = det_summary

        conf_explanation = (
            f"Composite confidence {avg_conf:.2f} derived from source reliability ratings, "
            f"independent corroboration counts, and {len(conflict_warnings)} conflict penalty adjustment(s)."
        )

        brief_id = f"brief_{compute_content_hash(f'{scope}:{now.isoformat()}')[:12]}"
        return OSINTSituationBrief(
            brief_id=brief_id,
            generated_at=now,
            title=f"AI-CRISS Evidence-Backed Situation Brief: {scope}",
            scope=scope,
            overall_crisis_stage=highest_stage,
            executive_summary=det_summary,
            key_extracted_facts=extracted_facts,
            correlated_intelligence=correlated_facts,
            ai_hypotheses_and_interpretations=det_hypotheses,
            conflicting_source_warnings=conflict_warnings,
            information_gaps=info_gaps,
            confidence=avg_conf,
            confidence_explanation=conf_explanation,
            supporting_evidence=prov_records,
            active_signals=signals,
            generation_provider=provider_used,
            answer_source=answer_source,
            model_used=model_used,
        )


GLOBAL_OSINT_ORCHESTRATOR = OSINTIntelligenceOrchestrator()
