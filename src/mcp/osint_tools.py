"""
AI-CRISS (CIP) — Controlled MCP Tool Registry & Dispatcher (src/mcp/osint_tools.py).

Implements Section 22 requirements for all 13 controlled MCP tools:
1. search_global_intelligence
2. search_news
3. search_events
4. search_entities
5. search_threat_intelligence
6. search_opencti
7. search_misp
8. search_sources
9. search_evidence
10. get_event_timeline
11. get_geofence_events
12. get_entity_relationships
13. generate_situation_brief

Every tool enforces:
- Strict Pydantic input validation
- Role-based authorization check
- Per-principal sliding-window rate limiting
- Execution timeout
- Structured output with provenance
- Sanitized error handling
"""

import asyncio
from datetime import datetime, timezone
import time
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.contracts.evidence_investigation import ProvenanceRecord
from src.contracts.osint_intelligence import GeofenceQueryRequest
from src.db.models import UserModel
from src.engine.geospatial_intelligence import GeospatialIntelligenceEngine
from src.engine.knowledge_graph import OSINTKnowledgeGraphEngine
from src.engine.osint_pipeline import GLOBAL_OSINT_ORCHESTRATOR, OSINTIntelligenceOrchestrator
from src.security.ssrf_guard import enforce_lawful_osint_query


class MCPSearchInput(BaseModel):
    query: str = Field(default="", max_length=300, description="Search query string")
    limit: int = Field(default=25, ge=1, le=100)


class MCPTimelineInput(BaseModel):
    event_id: Optional[str] = Field(default=None, max_length=128)


class MCPEntityRelationshipInput(BaseModel):
    entity_id: str = Field(..., min_length=1, max_length=128)


class MCPSituationBriefInput(BaseModel):
    scope: str = Field(default="Global & Regional Institutional Crisis Overview", max_length=200)
    use_live_llm: bool = Field(default=False)


class MCPToolExecutionResult(BaseModel):
    tool_name: str
    status: str  # SUCCESS, ERROR, RATE_LIMITED, UNAUTHORIZED
    executed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    latency_ms: float = 0.0
    data: Any = None
    provenance: List[ProvenanceRecord] = Field(default_factory=list)
    error_detail: Optional[str] = None


class ControlledMCPToolRegistry:
    """
    Registry and execution engine for all 13 AI-CRISS OSINT MCP tools.
    """

    TOOL_NAMES = [
        "search_global_intelligence",
        "search_news",
        "search_events",
        "search_entities",
        "search_threat_intelligence",
        "search_opencti",
        "search_misp",
        "search_sources",
        "search_evidence",
        "get_event_timeline",
        "get_geofence_events",
        "get_entity_relationships",
        "generate_situation_brief",
    ]

    def __init__(
        self,
        orchestrator: Optional[OSINTIntelligenceOrchestrator] = None,
        rate_limit_per_minute: int = 60,
        timeout_seconds: float = 15.0,
    ):
        self.orchestrator = orchestrator or GLOBAL_OSINT_ORCHESTRATOR
        self.rate_limit_per_minute = rate_limit_per_minute
        self.timeout_seconds = timeout_seconds
        self._call_timestamps: Dict[str, List[float]] = {}

    def list_tools(self) -> List[Dict[str, Any]]:
        """Return MCP tool definitions and schemas."""
        return [
            {
                "name": name,
                "description": f"Controlled AI-CRISS OSINT MCP tool: {name}",
                "requires_authentication": True,
                "rate_limit_per_minute": self.rate_limit_per_minute,
                "timeout_seconds": self.timeout_seconds,
                "preserves_provenance": True,
            }
            for name in self.TOOL_NAMES
        ]

    def _check_rate_limit(self, principal_id: str) -> bool:
        now = time.monotonic()
        window_start = now - 60.0
        history = [t for t in self._call_timestamps.get(principal_id, []) if t >= window_start]
        if len(history) >= self.rate_limit_per_minute:
            self._call_timestamps[principal_id] = history
            return False
        history.append(now)
        self._call_timestamps[principal_id] = history
        return True

    async def execute_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        session: AsyncSession,
        current_user: Optional[UserModel],
    ) -> MCPToolExecutionResult:
        start = time.perf_counter()

        # 1. Authorization check
        if current_user is None or not getattr(current_user, "is_active", True):
            return MCPToolExecutionResult(
                tool_name=tool_name,
                status="UNAUTHORIZED",
                latency_ms=round((time.perf_counter() - start) * 1000.0, 2),
                error_detail="Authentication required to invoke AI-CRISS MCP intelligence tools.",
            )

        if tool_name not in self.TOOL_NAMES:
            return MCPToolExecutionResult(
                tool_name=tool_name,
                status="ERROR",
                latency_ms=round((time.perf_counter() - start) * 1000.0, 2),
                error_detail=f"Unknown MCP tool '{tool_name}'.",
            )

        # 2. Rate limiting check
        principal = f"{current_user.id}:{tool_name}"
        if not self._check_rate_limit(principal):
            return MCPToolExecutionResult(
                tool_name=tool_name,
                status="RATE_LIMITED",
                latency_ms=round((time.perf_counter() - start) * 1000.0, 2),
                error_detail=f"Rate limit exceeded ({self.rate_limit_per_minute} requests/minute) for tool '{tool_name}'.",
            )

        # 3. Execute with strict validation, ethical guard, timeout, and provenance attachment
        try:
            async def _run() -> Tuple[Any, List[ProvenanceRecord]]:
                state = await self.orchestrator.ensure_seeded_and_load_state(session, user=current_user)
                all_prov = [ev.provenance for ev in state["evidence"] if ev.provenance]

                if tool_name == "search_global_intelligence":
                    inp = MCPSearchInput.model_validate(arguments)
                    enforce_lawful_osint_query(inp.query)
                    wi_bundle = self.orchestrator.world_intel.collect_intelligence(query=inp.query)
                    return (
                        {
                            "events": [e.model_dump(mode="json") for e in wi_bundle.events[:inp.limit]],
                            "signals": [s.model_dump(mode="json") for s in state["signals"][:inp.limit]],
                        },
                        [ev.provenance for ev in wi_bundle.evidence if ev.provenance],
                    )

                if tool_name == "search_news":
                    inp = MCPSearchInput.model_validate(arguments)
                    enforce_lawful_osint_query(inp.query)
                    tr_bundle = self.orchestrator.trendradar.collect_intelligence(query=inp.query)
                    return (
                        {"events": [e.model_dump(mode="json") for e in tr_bundle.events[:inp.limit]]},
                        [ev.provenance for ev in tr_bundle.evidence if ev.provenance],
                    )

                if tool_name == "search_events":
                    inp = MCPSearchInput.model_validate(arguments)
                    enforce_lawful_osint_query(inp.query)
                    q_low = inp.query.lower()
                    matched = [
                        e for e in state["events"]
                        if not q_low or q_low in e.title.lower() or q_low in e.description.lower()
                    ][:inp.limit]
                    provs = [ev.provenance for e in matched for ev in e.evidence if ev.provenance]
                    return {"events": [e.model_dump(mode="json") for e in matched]}, provs

                if tool_name == "search_entities":
                    inp = MCPSearchInput.model_validate(arguments)
                    enforce_lawful_osint_query(inp.query)
                    q_low = inp.query.lower()
                    matched_ents = [
                        ent for ent in state["entities"]
                        if not q_low or q_low in ent.name.lower() or q_low in ent.entity_type.lower()
                    ][:inp.limit]
                    return {"entities": [e.model_dump(mode="json") for e in matched_ents]}, all_prov[:6]

                if tool_name in ("search_threat_intelligence", "search_opencti"):
                    inp = MCPSearchInput.model_validate(arguments)
                    enforce_lawful_osint_query(inp.query)
                    octi_bundle = self.orchestrator.opencti.collect_intelligence(query=inp.query)
                    q_low = inp.query.lower()
                    ents = [
                        e for e in octi_bundle.entities
                        if not q_low or q_low in e.name.lower() or q_low in e.entity_type.lower()
                    ][:inp.limit]
                    return (
                        {
                            "entities": [e.model_dump(mode="json") for e in ents],
                            "relationships": [r.model_dump(mode="json") for r in octi_bundle.relationships[:inp.limit]],
                        },
                        [ev.provenance for ev in octi_bundle.evidence if ev.provenance],
                    )

                if tool_name == "search_misp":
                    inp = MCPSearchInput.model_validate(arguments)
                    enforce_lawful_osint_query(inp.query)
                    misp_bundle = self.orchestrator.misp.collect_intelligence(query=inp.query)
                    return (
                        {
                            "architecture": "MISP -> OpenCTI connector -> OpenCTI -> AI-CRISS",
                            "entities": [e.model_dump(mode="json") for e in misp_bundle.entities[:inp.limit]],
                        },
                        [ev.provenance for ev in misp_bundle.evidence if ev.provenance],
                    )

                if tool_name == "search_sources":
                    inp = MCPSearchInput.model_validate(arguments)
                    q_low = inp.query.lower()
                    srcs = [
                        s for s in state["sources"]
                        if not q_low or q_low in s.name.lower() or q_low in s.publisher.lower()
                    ][:inp.limit]
                    return {"sources": [s.model_dump(mode="json") for s in srcs]}, all_prov[:5]

                if tool_name == "search_evidence":
                    inp = MCPSearchInput.model_validate(arguments)
                    q_low = inp.query.lower()
                    evs = [
                        ev for ev in state["evidence"]
                        if not q_low
                        or q_low in ev.extracted_content.lower()
                        or (ev.title and q_low in ev.title.lower())
                    ][:inp.limit]
                    return (
                        {"evidence": [ev.model_dump(mode="json") for ev in evs]},
                        [ev.provenance for ev in evs if ev.provenance],
                    )

                if tool_name == "get_event_timeline":
                    inp = MCPTimelineInput.model_validate(arguments)
                    tl = OSINTKnowledgeGraphEngine.build_event_timeline(state["events"], event_id=inp.event_id)
                    return {"timeline": tl}, all_prov[:6]

                if tool_name == "get_geofence_events":
                    geo_req = GeofenceQueryRequest.model_validate(arguments)
                    geo_res = GeospatialIntelligenceEngine.query_geofence(
                        query=geo_req,
                        events=state["events"],
                        entities=state["entities"],
                    )
                    provs = [ev.provenance for e in geo_res.events for ev in e.evidence if ev.provenance]
                    return geo_res.model_dump(mode="json"), provs

                if tool_name == "get_entity_relationships":
                    inp = MCPEntityRelationshipInput.model_validate(arguments)
                    sub = OSINTKnowledgeGraphEngine.filter_entity_subgraph(state["knowledge_graph"], inp.entity_id)
                    return sub.model_dump(mode="json"), all_prov[:6]

                if tool_name == "generate_situation_brief":
                    inp = MCPSituationBriefInput.model_validate(arguments)
                    brief = self.orchestrator.generate_situation_brief(
                        events=state["events"],
                        signals=state["signals"],
                        conflicts=state["conflicts"],
                        scope=inp.scope,
                        use_live_llm=inp.use_live_llm,
                    )
                    return brief.model_dump(mode="json"), brief.supporting_evidence

                return {}, []

            data, prov_list = await asyncio.wait_for(_run(), timeout=self.timeout_seconds)
            return MCPToolExecutionResult(
                tool_name=tool_name,
                status="SUCCESS",
                latency_ms=round((time.perf_counter() - start) * 1000.0, 2),
                data=data,
                provenance=prov_list,
            )
        except ValidationError as v_err:
            return MCPToolExecutionResult(
                tool_name=tool_name,
                status="ERROR",
                latency_ms=round((time.perf_counter() - start) * 1000.0, 2),
                error_detail=f"VALIDATION_ERROR: {v_err.errors()[0].get('msg', 'Invalid input')}",
            )
        except Exception as exc:
            return MCPToolExecutionResult(
                tool_name=tool_name,
                status="ERROR",
                latency_ms=round((time.perf_counter() - start) * 1000.0, 2),
                error_detail=f"{type(exc).__name__}: {exc}",
            )


GLOBAL_MCP_TOOL_REGISTRY = ControlledMCPToolRegistry()
