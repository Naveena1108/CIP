"""
FastAPI Router for AI-CRISS (CIP) OSINT & Global Intelligence Subsystem (`/api/v1/osint/*`).

Exposes authenticated, RBAC-protected, SSRF-guarded endpoints for:
- Provider Observability & Integration Health (`/osint/health`)
- Unified Intelligence Dashboard (`/osint/dashboard`)
- Multi-Provider Collection (`/osint/collect`)
- Events, Sources, Evidence, Entities, Threat Intelligence (OpenCTI/MISP)
- Temporal Timeline (`/osint/timeline`)
- Geospatial & Geofenced Crisis Monitoring (`/osint/geofence`)
- Knowledge Graph (`/osint/knowledge-graph`)
- Evidence-Backed Situation Briefs (`/osint/briefs`)
- SSRF-Protected Web Extraction (`/osint/web-extract`)
- Controlled MCP Tool Registry & Execution (`/osint/mcp/tools`, `/osint/mcp/execute`)
"""

from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import get_current_user
from src.contracts.osint_intelligence import (
    GeofenceIntelligenceResponse,
    GeofenceQueryRequest,
    IntegrationStatusDashboard,
    KnowledgeGraphSnapshot,
    OSINTSituationBrief,
)
from src.db.models import InstitutionModel, UserModel
from src.db.osint_repository import OSINTRepository
from src.db.session import get_db_session
from src.engine.geospatial_intelligence import GeospatialIntelligenceEngine
from src.engine.knowledge_graph import OSINTKnowledgeGraphEngine
from src.engine.osint_pipeline import GLOBAL_OSINT_ORCHESTRATOR
from src.integrations.optional_modules.registry import list_optional_osint_modules
from src.mcp.osint_tools import GLOBAL_MCP_TOOL_REGISTRY, MCPToolExecutionResult
from src.security.ssrf_guard import (
    SSRFViolationError,
    UnethicalOSINTRequestError,
    enforce_lawful_osint_query,
)

router = APIRouter(prefix="/osint", tags=["OSINT & Global Crisis Intelligence"])


class CollectOSINTRequest(BaseModel):
    query: Optional[str] = Field(default=None, max_length=300)
    organization_id: Optional[str] = None
    institution_id: Optional[str] = None
    include_optional_openosint: bool = False


class WebExtractRequest(BaseModel):
    url: str = Field(..., min_length=4, max_length=1000)
    html_content: Optional[str] = Field(
        default=None,
        description="Optional raw HTML to parse directly after SSRF URL validation",
    )


class SituationBriefRequest(BaseModel):
    scope: str = Field(default="Global & Regional Institutional Crisis Overview", max_length=250)
    use_live_llm: bool = Field(default=False)


class MCPExecuteRequest(BaseModel):
    tool_name: str = Field(..., min_length=1, max_length=128)
    arguments: Dict[str, Any] = Field(default_factory=dict)


@router.get("/health", response_model=IntegrationStatusDashboard)
async def get_osint_providers_health(
    current_user: UserModel = Depends(get_current_user),
):
    """Return the Section 25 Integration Status Dashboard across all OSINT providers."""
    return GLOBAL_OSINT_ORCHESTRATOR.get_integration_status_dashboard()


@router.post("/collect")
async def trigger_osint_collection(
    req: CollectOSINTRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """Run the 15-stage OSINT intelligence pipeline across configured providers and persist results."""
    try:
        enforce_lawful_osint_query(req.query or "")
    except UnethicalOSINTRequestError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    result = await GLOBAL_OSINT_ORCHESTRATOR.collect_and_persist_all(
        session=session,
        query=req.query,
        organization_id=req.organization_id or current_user.organization_id,
        institution_id=req.institution_id or current_user.primary_institution_id,
        include_optional_openosint=req.include_optional_openosint,
    )
    return {
        "status": "COLLECTION_COMPLETED",
        "pipeline_stages_completed": result["pipeline_stages_completed"],
        "sources_count": len(result["sources"]),
        "evidence_count": len(result["evidence"]),
        "entities_count": len(result["entities"]),
        "events_count": len(result["events"]),
        "signals_count": len(result["signals"]),
        "conflicts_count": len(result["conflicts"]),
    }


@router.get("/dashboard")
async def get_osint_intelligence_dashboard(
    organization_id: Optional[str] = Query(default=None),
    institution_id: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """
    Return the unified Section 30 Intelligence Dashboard payload:
    - ACTIVE CRISIS SIGNALS
    - RECENT EVENTS
    - GLOBAL HOTSPOTS & GEOGRAPHIC CLUSTERS
    - EVENT TIMELINE
    - SOURCE ACTIVITY
    - THREAT INTELLIGENCE (OpenCTI / MISP STIX)
    - CORRELATED ENTITIES & KNOWLEDGE GRAPH
    - CONFIDENCE, EVIDENCE & CONFLICTS
    """
    state = await GLOBAL_OSINT_ORCHESTRATOR.ensure_seeded_and_load_state(
        session=session,
        user=current_user,
        organization_id=organization_id,
        institution_id=institution_id,
    )
    inst_rows = (await session.execute(select(InstitutionModel))).scalars().all()
    inst_dicts = [
        {"id": i.id, "name": i.name, "city": i.city, "state": i.state, "latitude": i.latitude, "longitude": i.longitude}
        for i in inst_rows
    ]

    clusters = GeospatialIntelligenceEngine.compute_density_clusters(state["events"], inst_dicts)
    timeline = OSINTKnowledgeGraphEngine.build_event_timeline(state["events"])
    brief = GLOBAL_OSINT_ORCHESTRATOR.generate_situation_brief(
        events=state["events"],
        signals=state["signals"],
        conflicts=state["conflicts"],
    )
    health = GLOBAL_OSINT_ORCHESTRATOR.get_integration_status_dashboard()

    threat_entities = [
        e for e in state["entities"]
        if e.entity_type in ("Threat Actor", "Intrusion Set", "Malware", "Vulnerability", "Indicator", "Observable", "Campaign", "Infrastructure")
    ]

    return {
        "active_crisis_signals": [s.model_dump(mode="json") for s in state["signals"]],
        "recent_events": [e.model_dump(mode="json") for e in state["events"]],
        "global_hotspots": [c.model_dump(mode="json") for c in clusters],
        "geographic_clusters": [c.model_dump(mode="json") for c in clusters],
        "event_timeline": timeline,
        "source_activity": [s.model_dump(mode="json") for s in state["sources"]],
        "threat_intelligence": {
            "entities": [e.model_dump(mode="json") for e in threat_entities],
            "relationships": [r.model_dump(mode="json") for r in state["relationships"]],
        },
        "correlated_entities": [e.model_dump(mode="json") for e in state["entities"]],
        "knowledge_graph": state["knowledge_graph"].model_dump(mode="json"),
        "conflicts": [c.model_dump(mode="json") for c in state["conflicts"]],
        "evidence": [ev.model_dump(mode="json") for ev in state["evidence"]],
        "situation_brief": brief.model_dump(mode="json"),
        "provider_status": health.model_dump(mode="json"),
        "optional_modules": list_optional_osint_modules(),
    }


@router.get("/events")
async def list_osint_events(
    event_type: Optional[str] = Query(default=None),
    severity: Optional[str] = Query(default=None),
    query: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    await GLOBAL_OSINT_ORCHESTRATOR.ensure_seeded_and_load_state(session, user=current_user)
    events = await OSINTRepository.list_events(
        session=session,
        user=current_user,
        event_type=event_type,
        severity=severity,
        query=query,
    )
    return {"total": len(events), "events": [e.model_dump(mode="json") for e in events]}


@router.get("/sources")
async def list_osint_sources(
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    await GLOBAL_OSINT_ORCHESTRATOR.ensure_seeded_and_load_state(session, user=current_user)
    sources = await OSINTRepository.list_sources(session)
    return {"total": len(sources), "sources": [s.model_dump(mode="json") for s in sources]}


@router.get("/evidence")
async def list_osint_evidence(
    source_id: Optional[str] = Query(default=None),
    query: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    await GLOBAL_OSINT_ORCHESTRATOR.ensure_seeded_and_load_state(session, user=current_user)
    evidence = await OSINTRepository.list_evidence(session, source_id=source_id, query=query)
    return {"total": len(evidence), "evidence": [ev.model_dump(mode="json") for ev in evidence]}


@router.get("/entities")
async def list_osint_entities(
    entity_type: Optional[str] = Query(default=None),
    query: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    await GLOBAL_OSINT_ORCHESTRATOR.ensure_seeded_and_load_state(session, user=current_user)
    entities = await OSINTRepository.list_entities(
        session=session,
        user=current_user,
        entity_type=entity_type,
        query=query,
    )
    return {"total": len(entities), "entities": [e.model_dump(mode="json") for e in entities]}


@router.get("/threat-intelligence")
async def get_threat_intelligence_view(
    query: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """Return STIX 2.1 Cyber Threat Intelligence from OpenCTI and MISP connector bridge."""
    try:
        enforce_lawful_osint_query(query or "")
    except UnethicalOSINTRequestError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    octi_bundle = GLOBAL_OSINT_ORCHESTRATOR.opencti.collect_intelligence(query=query)
    misp_bundle = GLOBAL_OSINT_ORCHESTRATOR.misp.collect_intelligence(query=query)
    return {
        "misp_architecture": "MISP -> OpenCTI connector -> OpenCTI -> AI-CRISS intelligence adapter",
        "opencti_entities": [e.model_dump(mode="json") for e in octi_bundle.entities],
        "stix_relationships": [r.model_dump(mode="json") for r in octi_bundle.relationships],
        "misp_correlated_indicators": [e.model_dump(mode="json") for e in misp_bundle.entities],
        "evidence": [ev.model_dump(mode="json") for ev in octi_bundle.evidence],
    }


@router.get("/timeline")
async def get_osint_event_timeline(
    event_id: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    state = await GLOBAL_OSINT_ORCHESTRATOR.ensure_seeded_and_load_state(session, user=current_user)
    tl = OSINTKnowledgeGraphEngine.build_event_timeline(state["events"], event_id=event_id)
    return {"total": len(tl), "timeline": tl}


@router.post("/geofence", response_model=GeofenceIntelligenceResponse)
async def query_geofenced_events(
    req: GeofenceQueryRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """
    Answer geospatial radius & temporal queries such as:
    'What significant events occurred within 100 km of this location during the last 24 hours?'
    """
    state = await GLOBAL_OSINT_ORCHESTRATOR.ensure_seeded_and_load_state(session, user=current_user)
    inst_rows = (await session.execute(select(InstitutionModel))).scalars().all()
    inst_dicts = [
        {"id": i.id, "name": i.name, "city": i.city, "state": i.state, "latitude": i.latitude, "longitude": i.longitude}
        for i in inst_rows
    ]
    return GeospatialIntelligenceEngine.query_geofence(
        query=req,
        events=state["events"],
        entities=state["entities"],
        institutions=inst_dicts,
    )


@router.get("/knowledge-graph", response_model=KnowledgeGraphSnapshot)
async def get_osint_knowledge_graph(
    entity_id: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    state = await GLOBAL_OSINT_ORCHESTRATOR.ensure_seeded_and_load_state(session, user=current_user)
    graph: KnowledgeGraphSnapshot = state["knowledge_graph"]
    if entity_id:
        return OSINTKnowledgeGraphEngine.filter_entity_subgraph(graph, entity_id)
    return graph


@router.post("/briefs", response_model=OSINTSituationBrief)
async def generate_osint_situation_brief(
    req: SituationBriefRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    state = await GLOBAL_OSINT_ORCHESTRATOR.ensure_seeded_and_load_state(session, user=current_user)
    return GLOBAL_OSINT_ORCHESTRATOR.generate_situation_brief(
        events=state["events"],
        signals=state["signals"],
        conflicts=state["conflicts"],
        scope=req.scope,
        use_live_llm=req.use_live_llm,
    )


@router.post("/web-extract")
async def extract_web_intelligence(
    req: WebExtractRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """
    SSRF-protected web article and metadata extraction endpoint.
    Rejects private/loopback/metadata IPs and forbidden schemes with HTTP 400.
    """
    try:
        enforce_lawful_osint_query(req.url)
        if req.html_content is not None:
            bundle = GLOBAL_OSINT_ORCHESTRATOR.web_crawler.extract_from_html(req.url, req.html_content)
        else:
            bundle = GLOBAL_OSINT_ORCHESTRATOR.web_crawler.extract_url(req.url, resolve_dns=True)
    except (SSRFViolationError, UnethicalOSINTRequestError) as sec_exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(sec_exc))

    for s in bundle.sources:
        await OSINTRepository.upsert_source(session, s)
    for ev in bundle.evidence:
        await OSINTRepository.upsert_evidence(session, ev)
    for evt in bundle.events:
        await OSINTRepository.upsert_event(session, evt)
    await session.flush()

    return {
        "status": "EXTRACTED" if not bundle.error_message else "DEGRADED",
        "sources": [s.model_dump(mode="json") for s in bundle.sources],
        "evidence": [ev.model_dump(mode="json") for ev in bundle.evidence],
        "events": [e.model_dump(mode="json") for e in bundle.events],
        "error": bundle.error_message,
    }


@router.get("/mcp/tools")
async def list_mcp_osint_tools(
    current_user: UserModel = Depends(get_current_user),
):
    return {"tools": GLOBAL_MCP_TOOL_REGISTRY.list_tools()}


@router.post("/mcp/execute", response_model=MCPToolExecutionResult)
async def execute_mcp_osint_tool(
    req: MCPExecuteRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    res = await GLOBAL_MCP_TOOL_REGISTRY.execute_tool(
        tool_name=req.tool_name,
        arguments=req.arguments,
        session=session,
        current_user=current_user,
    )
    if res.status == "RATE_LIMITED":
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=res.error_detail)
    if res.status == "ERROR" and res.error_detail and "LEGAL_ETHICAL_BOUNDARY_VIOLATION" in res.error_detail:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=res.error_detail)
    return res
