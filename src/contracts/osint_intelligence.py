"""
AI-CRISS (CIP) — OSINT & Global Intelligence Domain Contracts.

Implements the strict canonical models required for:
- Source (id, name, type, url, publisher, collection_method, reliability, timestamp, license_metadata)
- Evidence (id, source_id, content_hash, extracted_content, captured_at, published_at, provenance, confidence, extraction_method)
- Entity (Person, Organization, Location, Country, City, Infrastructure, Threat Actor, Malware, Vulnerability, Event, Asset, Indicator, Intrusion Set, Campaign)
- Event (id, title, description, event_type, start_time, end_time, location, entities, severity, confidence, source_count, evidence, status + temporal & geospatial fields)
- Relationship (source_entity, relationship_type, target_entity, confidence, evidence, timestamp)
- Epistemic Provenance Chain (RAW_SOURCE -> EXTRACTED_FACT -> CORRELATED_FACT -> AI_INTERPRETATION -> AI_ASSESSMENT)
- Event Deduplication (unique, candidate_duplicate, probable_duplicate, confirmed_duplicate)
- Source Corroboration (Single-source signal, Multi-source corroborated signal, Official-source confirmation, Conflicting-source signal)
- Conflicting Information Preservation (source_a_claim, source_b_claim, disagreement, timestamps, evidence, confidence, resolution_status)
- 5-Stage Crisis Signal Model (OBSERVATION -> SIGNAL -> DEVELOPING_EVENT -> SIGNIFICANT_EVENT -> CRISIS_CANDIDATE)
- Knowledge Graph, Geofenced Crisis Monitoring, Situation Briefs, and Provider Health Observability.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

from src.contracts.evidence_investigation import ProvenanceRecord


OSINTEntityType = Literal[
    "Person",
    "Organization",
    "Location",
    "Country",
    "City",
    "Infrastructure",
    "Threat Actor",
    "Intrusion Set",
    "Campaign",
    "Malware",
    "Vulnerability",
    "Event",
    "Asset",
    "Indicator",
    "Observable",
]

OSINTEventType = Literal[
    "geopolitical",
    "conflict",
    "military",
    "cyber_threat",
    "climate_environmental",
    "supply_chain",
    "infrastructure_disruption",
    "humanitarian",
    "institutional_governance",
    "regulatory_policy",
    "public_safety",
    "general_osint",
]

OSINTSeverity = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]

DuplicateMatchStatus = Literal[
    "unique",
    "candidate_duplicate",
    "probable_duplicate",
    "confirmed_duplicate",
]

CorroborationStatus = Literal[
    "Single-source signal",
    "Multi-source corroborated signal",
    "Official-source confirmation",
    "Conflicting-source signal",
]

CrisisSignalStage = Literal[
    "OBSERVATION",
    "SIGNAL",
    "DEVELOPING_EVENT",
    "SIGNIFICANT_EVENT",
    "CRISIS_CANDIDATE",
]

CrisisSignalCategory = Literal[
    "sudden_event_spike",
    "geographic_clustering",
    "repeated_reports",
    "unusual_terminology",
    "escalation_indicator",
    "infrastructure_disruption",
    "cyber_indicator",
    "supply_chain_disruption",
    "humanitarian_indicator",
    "conflict_indicator",
    "environmental_indicator",
]

ProviderHealthState = Literal["HEALTHY", "DEGRADED", "OFFLINE", "UNCONFIGURED"]


class EpistemicClaimSeparation(BaseModel):
    """
    Mandatory 5-tier epistemic separation for every intelligence event/claim.
    Never collapses RAW SOURCE, EXTRACTED FACT, CORRELATED FACT, AI INTERPRETATION,
    and AI ASSESSMENT into a single field.
    """
    raw_source: List[str] = Field(
        default_factory=list,
        description="Verbatim raw source excerpts, feed payloads, or STIX bundles with source IDs",
    )
    extracted_facts: List[str] = Field(
        default_factory=list,
        description="Deterministic facts directly extracted from raw sources (timestamps, locations, indicators)",
    )
    correlated_facts: List[str] = Field(
        default_factory=list,
        description="Deterministic cross-source or spatial-temporal correlations verified by engine rules",
    )
    ai_interpretation: List[str] = Field(
        default_factory=list,
        description="Bounded AI or analytical hypotheses synthesizing extracted/correlated facts (never stored as primary fact)",
    )
    ai_assessment: Optional[str] = Field(
        default=None,
        description="Evidence-backed executive crisis assessment explicitly citing provenance and uncertainty",
    )


class OSINTSource(BaseModel):
    """Canonical intelligence source metadata."""
    id: str = Field(..., description="Unique source identifier")
    name: str = Field(..., description="Human-readable source name (e.g., Reuters, CISA, OpenCTI, World Intel MCP)")
    type: Literal[
        "world_intel_mcp",
        "opencti",
        "misp",
        "trendradar_rss",
        "web_crawler",
        "openosint",
        "official_government",
        "geospatial_monitor",
        "optional_osint_module",
    ]
    url: Optional[str] = Field(default=None, description="Canonical source URL or feed endpoint")
    publisher: str = Field(..., description="Publishing organization or syndicate")
    independence_group: str = Field(
        default="",
        description="Root syndicate/origin group used to detect syndication echoes vs independent corroboration",
    )
    is_official_source: bool = Field(
        default=False,
        description="True for verified government/regulatory/CERT advisories",
    )
    collection_method: str = Field(..., description="Adapter/protocol used to collect intelligence")
    reliability: float = Field(default=0.8, ge=0.0, le=1.0, description="Source reliability rating [0.0, 1.0]")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    license_metadata: Dict[str, Any] = Field(
        default_factory=lambda: {"license": "public_source_osint", "terms_respected": True}
    )


class OSINTEvidence(BaseModel):
    """Immutable, content-hashed intelligence evidence record."""
    id: str = Field(..., description="Unique evidence identifier")
    source_id: str = Field(..., description="Reference to OSINTSource.id")
    source_url: Optional[str] = Field(default=None, description="Specific article, advisory, or STIX report URL")
    content_hash: str = Field(..., description="SHA-256 hex digest of normalized extracted content")
    extracted_content: str = Field(..., description="Sanitized extracted text or structured observation")
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    published_at: Optional[datetime] = Field(default=None)
    provenance: ProvenanceRecord = Field(..., description="7-field CIP ProvenanceRecord for unified UI inspection")
    confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    extraction_method: str = Field(..., description="Extraction adapter and parser version")
    author: Optional[str] = None
    language: str = "en"
    title: Optional[str] = None
    relevant_links: List[str] = Field(default_factory=list)


class OSINTLocation(BaseModel):
    """Geospatial coordinate and administrative location representation."""
    latitude: Optional[float] = Field(default=None, ge=-90.0, le=90.0)
    longitude: Optional[float] = Field(default=None, ge=-180.0, le=180.0)
    country: Optional[str] = None
    region: Optional[str] = None
    city: Optional[str] = None
    radius_km: Optional[float] = Field(default=None, ge=0.0)
    precision: Literal["exact_coordinates", "city_level", "region_level", "country_level", "unknown"] = "unknown"


class OSINTEntity(BaseModel):
    """Canonical entity model across physical, organizational, and STIX cyber-threat domains."""
    id: str
    name: str
    entity_type: OSINTEntityType
    aliases: List[str] = Field(default_factory=list)
    stix_id: Optional[str] = Field(default=None, description="STIX 2.1 identifier when sourced from OpenCTI/MISP")
    description: Optional[str] = None
    confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    location: Optional[OSINTLocation] = None
    attributes: Dict[str, Any] = Field(default_factory=dict)
    source_references: List[str] = Field(default_factory=list)
    evidence_ids: List[str] = Field(default_factory=list)
    organization_id: Optional[str] = None
    institution_id: Optional[str] = None


class OSINTRelationship(BaseModel):
    """Directed relationship edge with provenance and temporal bounds."""
    id: str
    source_entity: str = Field(..., description="Source OSINTEntity.id or node ID")
    relationship_type: str = Field(
        ...,
        description="STIX or domain relationship (e.g., targets, uses, attributed-to, located-in, affects, reported-by, supported-by, mitigates)",
    )
    target_entity: str = Field(..., description="Target OSINTEntity.id or node ID")
    confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    evidence: List[str] = Field(default_factory=list, description="Supporting OSINTEvidence.id list")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None


class SourceConflictRecord(BaseModel):
    """
    Explicitly preserves disagreements when intelligence sources conflict.
    Never silently chooses one source over another.
    """
    conflict_id: str
    event_id: str
    topic_or_field: str
    source_a_id: str
    source_a_claim: str
    source_b_id: str
    source_b_claim: str
    disagreement: str
    source_a_timestamp: Optional[datetime] = None
    source_b_timestamp: Optional[datetime] = None
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    evidence: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    resolution_status: Literal["UNRESOLVED", "PARTIALLY_CORROBORATED", "RESOLVED_BY_OFFICIAL_SOURCE"] = "UNRESOLVED"


class OSINTEvent(BaseModel):
    """
    Canonical global/crisis intelligence event with temporal lifecycle, geospatial coordinates,
    deduplication fingerprint, corroboration status, conflict records, and 5-tier epistemic separation.
    """
    id: str
    fingerprint: str = Field(..., description="Deterministic deduplication fingerprint across title/entities/location/time window")
    title: str
    description: str
    event_type: OSINTEventType = "general_osint"
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    # Temporal Lifecycle (Section 18)
    first_observed: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reported_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    confirmed_at: Optional[datetime] = None
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: Optional[datetime] = None

    # Geospatial (Section 17)
    location: OSINTLocation = Field(default_factory=OSINTLocation)

    # Entities, Severity, Confidence & Sources
    entities: List[str] = Field(default_factory=list, description="Linked OSINTEntity IDs")
    severity: OSINTSeverity = "MEDIUM"
    confidence: float = Field(default=0.75, ge=0.0, le=1.0)
    source_count: int = Field(default=1, ge=1)
    independent_source_count: int = Field(default=1, ge=1)
    source_ids: List[str] = Field(default_factory=list)
    origin_hashes: List[str] = Field(
        default_factory=list,
        description="Content/origin hashes to detect syndicated re-posts of the same original wire report",
    )
    evidence: List[OSINTEvidence] = Field(default_factory=list)
    status: Literal["ACTIVE", "MONITORING", "CONFIRMED", "CONTESTED", "RESOLVED"] = "ACTIVE"

    # Deduplication & Corroboration (Sections 14–16)
    duplicate_status: DuplicateMatchStatus = "unique"
    matched_event_id: Optional[str] = None
    duplicate_similarity: float = Field(default=0.0, ge=0.0, le=1.0)
    corroboration_status: CorroborationStatus = "Single-source signal"
    conflicts: List[SourceConflictRecord] = Field(default_factory=list)

    # Staged Crisis Classification (Section 20)
    signal_stage: CrisisSignalStage = "OBSERVATION"

    # Mandatory 5-Tier Epistemic Separation (Section 13)
    epistemic_chain: EpistemicClaimSeparation = Field(default_factory=EpistemicClaimSeparation)

    organization_id: Optional[str] = None
    institution_id: Optional[str] = None


class CrisisSignal(BaseModel):
    """
    Output of the Crisis Signal Engine (Section 20).
    Uses the staged progression model:
    OBSERVATION -> SIGNAL -> DEVELOPING_EVENT -> SIGNIFICANT_EVENT -> CRISIS_CANDIDATE
    """
    signal_id: str
    title: str
    category: CrisisSignalCategory
    stage: CrisisSignalStage
    severity: OSINTSeverity
    confidence: float = Field(..., ge=0.0, le=1.0)
    rationale: str
    evidence: List[ProvenanceRecord] = Field(default_factory=list)
    sources: List[str] = Field(default_factory=list)
    location: OSINTLocation = Field(default_factory=OSINTLocation)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    related_entities: List[str] = Field(default_factory=list)
    related_events: List[str] = Field(default_factory=list)
    has_conflicting_sources: bool = False
    conflict_notes: List[str] = Field(default_factory=list)


class KnowledgeGraphNode(BaseModel):
    """Node in the provenance-backed OSINT & Institutional Knowledge Graph."""
    node_id: str
    node_type: Literal[
        "Event",
        "Entity",
        "Location",
        "Source",
        "Evidence",
        "Indicator",
        "Threat Actor",
        "Organization",
        "Infrastructure",
        "Timeline",
    ]
    label: str
    attributes: Dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    provenance_ids: List[str] = Field(default_factory=list)


class KnowledgeGraphEdge(BaseModel):
    """Directed edge in the Knowledge Graph with provenance preservation."""
    edge_id: str
    source_node_id: str
    relationship_type: str
    target_node_id: str
    confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    evidence_ids: List[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class KnowledgeGraphSnapshot(BaseModel):
    """Complete or filtered subgraph snapshot."""
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    total_nodes: int
    total_edges: int
    nodes: List[KnowledgeGraphNode] = Field(default_factory=list)
    edges: List[KnowledgeGraphEdge] = Field(default_factory=list)


class GeofenceQueryRequest(BaseModel):
    """Geospatial radius & temporal window query."""
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    radius_km: float = Field(default=100.0, gt=0.0, le=20000.0)
    hours_back: float = Field(default=24.0, gt=0.0, le=8760.0)
    min_severity: Optional[OSINTSeverity] = None


class GeofenceCluster(BaseModel):
    """Geographic event density cluster."""
    cluster_id: str
    center_latitude: float
    center_longitude: float
    country: Optional[str] = None
    region_or_city: Optional[str] = None
    event_count: int
    max_severity: OSINTSeverity
    highest_stage: CrisisSignalStage
    event_ids: List[str] = Field(default_factory=list)
    nearby_institutions_or_assets: List[str] = Field(default_factory=list)


class GeofenceIntelligenceResponse(BaseModel):
    """Response for 'What significant events occurred within R km during the last T hours?'"""
    query: GeofenceQueryRequest
    matching_event_count: int
    events: List[OSINTEvent] = Field(default_factory=list)
    event_distances_km: Dict[str, float] = Field(default_factory=dict)
    density_clusters: List[GeofenceCluster] = Field(default_factory=list)
    nearby_infrastructure_and_institutions: List[Dict[str, Any]] = Field(default_factory=list)
    summary: str


class OSINTSituationBrief(BaseModel):
    """Evidence-backed AI/Deterministic Crisis Situation Brief (Sections 19 & 30)."""
    brief_id: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    title: str
    scope: str
    overall_crisis_stage: CrisisSignalStage
    executive_summary: str
    key_extracted_facts: List[str] = Field(default_factory=list)
    correlated_intelligence: List[str] = Field(default_factory=list)
    ai_hypotheses_and_interpretations: List[str] = Field(default_factory=list)
    conflicting_source_warnings: List[str] = Field(default_factory=list)
    information_gaps: List[str] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)
    confidence_explanation: str
    supporting_evidence: List[ProvenanceRecord] = Field(default_factory=list)
    active_signals: List[CrisisSignal] = Field(default_factory=list)
    generation_provider: str = "deterministic_fallback"
    answer_source: str = "deterministic fallback"
    model_used: str = "DETERMINISTIC_FALLBACK_ENGINE"


class ProviderObservabilityItem(BaseModel):
    """Health and telemetry status for a single OSINT integration provider (Section 25)."""
    provider_id: str
    display_name: str
    priority: int
    status: ProviderHealthState
    configured: bool
    live_endpoint_reachable: bool
    fallback_mode_active: bool
    request_count: int = 0
    ingestion_count: int = 0
    error_count: int = 0
    failed_jobs_count: int = 0
    avg_latency_ms: float = 0.0
    last_checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_error: Optional[str] = None
    capabilities: List[str] = Field(default_factory=list)


class IntegrationStatusDashboard(BaseModel):
    """Aggregated observability dashboard across all OSINT providers (Section 25)."""
    overall_status: Literal["HEALTHY", "DEGRADED", "OPERATIONAL_WITH_FALLBACKS"]
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    providers: List[ProviderObservabilityItem] = Field(default_factory=list)
    cache_stats: Dict[str, Any] = Field(default_factory=dict)
