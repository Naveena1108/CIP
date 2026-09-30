"""Comprehensive Unit, Integration, and Security Tests for the AI-CRISS OSINT & Global Intelligence Subsystem.

Covers all 10 mandatory testing areas from Section 34 plus Priority 1-7 adapters,
15-stage pipeline, Controlled MCP tools, SSRF/XXE/Ethical guardrails, and FastAPI routes.
"""

from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.contracts.evidence_investigation import ProvenanceRecord
from src.contracts.osint_intelligence import (
    EpistemicClaimSeparation,
    GeofenceQueryRequest,
    OSINTEntity,
    OSINTEvent,
    OSINTEvidence,
    OSINTLocation,
    OSINTRelationship,
    OSINTSource,
)
from src.db.models import UserModel
from src.db.session import async_session_factory, init_db
from src.engine.crisis_signal_engine import (
    CrisisSignalEngine,
    OSINTDeduplicationAndCorroborationEngine,
)
from src.engine.geospatial_intelligence import (
    GeospatialIntelligenceEngine,
    haversine_distance_km,
)
from src.engine.knowledge_graph import OSINTKnowledgeGraphEngine
from src.engine.osint_pipeline import OSINTIntelligenceOrchestrator
from src.integrations.misp.adapter import MISPConfig, MISPIntegrationAdapter
from src.integrations.opencti.adapter import (
    OpenCTIConfig,
    OpenCTIQueryAdapter,
    STIXEntityMapper,
)
from src.integrations.openosint.adapter import OpenOSINTInvestigationAdapter
from src.integrations.optional_modules.registry import list_optional_osint_modules
from src.integrations.trendradar.adapter import TrendRadarAdapter, TrendRadarConfig
from src.integrations.web_crawler.adapter import WebCrawlerAdapter, WebCrawlerConfig
from src.integrations.world_intel.adapter import WorldIntelConfig, WorldIntelMCPAdapter
from src.mcp.osint_tools import ControlledMCPToolRegistry
from src.security.ssrf_guard import (
    SSRFViolationError,
    UnethicalOSINTRequestError,
    enforce_lawful_osint_query,
    safe_parse_xml,
    validate_external_url,
)


def _make_provenance(ev_id: str, src_id: str) -> ProvenanceRecord:
    return ProvenanceRecord(
        evidence_id=ev_id,
        source=src_id,
        document=f"https://example.org/{ev_id}",
        page_or_section="Section 1",
        table_cell_or_range="Line 1",
        excerpt_or_image="Verified excerpt",
        extraction_confidence=0.9,
        date_or_context="2026-09-30",
        domain="osint",
        metric_name="osint_event",
    )


# ==============================================================================
# 1. PRIORITY 1-7 ADAPTERS & NORMALIZATION TESTS
# ==============================================================================


def test_priority_1_world_intel_mcp_adapter_normalization():
    adapter = WorldIntelMCPAdapter(WorldIntelConfig())
    bundle = adapter.collect_intelligence(query="infrastructure")
    assert len(bundle.sources) >= 1
    assert len(bundle.evidence) >= 1
    assert len(bundle.events) >= 1
    assert len(bundle.entities) >= 1
    evt = bundle.events[0]
    assert evt.location.latitude is not None
    assert evt.epistemic_chain.raw_source
    assert evt.epistemic_chain.extracted_facts


def test_priority_2_opencti_stix_mapper_and_query_adapter():
    adapter = OpenCTIQueryAdapter(OpenCTIConfig(base_url="", api_token=""))
    stix_bundle = {
        "objects": [
            {
                "id": "threat-actor--8e2e2d2b-17d4-4cbf-938f-98ee46b3cd3f",
                "type": "threat-actor",
                "name": "APT-EduShadow",
                "description": "Threat group targeting university research repositories.",
                "confidence": 85,
                "first_seen": "2026-09-01T00:00:00Z",
                "last_seen": "2026-09-29T12:00:00Z",
                "aliases": ["ScholarBear"],
                "external_references": [
                    {
                        "source_name": "CERT Advisory",
                        "url": "https://cert.example.org/adv-2026-09",
                        "description": "Confirmed credential harvesting campaign against academic portals.",
                    }
                ],
            },
            {
                "id": "vulnerability--11112222-3333-4444-5555-666677778888",
                "type": "vulnerability",
                "name": "CVE-2026-4099",
                "description": "Remote code execution in campus SSO gateway.",
                "confidence": 90,
            },
            {
                "id": "relationship--aaaa1111-bbbb-2222-cccc-333344445555",
                "type": "relationship",
                "relationship_type": "targets",
                "source_ref": "threat-actor--8e2e2d2b-17d4-4cbf-938f-98ee46b3cd3f",
                "target_ref": "vulnerability--11112222-3333-4444-5555-666677778888",
                "confidence": 88,
            },
        ]
    }
    bundle = adapter.ingest_stix_bundle(stix_bundle, collection_mode="unit_test")
    assert len(bundle.entities) >= 2
    assert len(bundle.relationships) == 1
    ta = [e for e in bundle.entities if e.entity_type == "Threat Actor"][0]
    assert ta.name == "APT-EduShadow"
    assert ta.confidence == 0.85
    assert "ScholarBear" in ta.aliases
    rel = bundle.relationships[0]
    assert rel.relationship_type == "targets"


def test_priority_3_misp_via_opencti_and_direct_bridge_fallback():
    opencti_adapter = OpenCTIQueryAdapter(OpenCTIConfig())
    misp_adapter = MISPIntegrationAdapter(
        config=MISPConfig(use_opencti_connector=True),
        opencti_adapter=opencti_adapter,
    )
    health = misp_adapter.health_check()
    assert health.provider_id == "misp"
    assert "opencti_misp_connector_bridge" in health.capabilities

    misp_event = {
        "Event": {
            "uuid": "5c8a1111-2222-3333-4444-555566667777",
            "info": "Academic Portal Phishing Wave",
            "date": "2026-09-28",
            "Attribute": [
                {
                    "uuid": "attr-001",
                    "type": "domain",
                    "value": "login-campus-verify.example.net",
                    "comment": "Phishing domain spoofing university login",
                    "to_ids": True,
                }
            ],
        }
    }
    stix_bundle = misp_adapter.convert_standalone_misp_event_to_stix_bundle(misp_event)
    assert "objects" in stix_bundle
    assert len(stix_bundle["objects"]) >= 2

    bundle = misp_adapter.collect_intelligence(query="phishing")
    assert len(bundle.entities) >= 1


def test_priority_4_trendradar_rss_parsing_and_trend_detection():
    adapter = TrendRadarAdapter(TrendRadarConfig())
    rss_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>Higher Education Regulatory News</title>
        <item>
          <title>Severe Flood Warning Disrupts Regional Transport and Examinations</title>
          <link>https://news-alpha.example.org/flood-warning-1</link>
          <description>Heavy monsoon rainfall prompts emergency flood advisory across regional examination centers.</description>
          <pubDate>Wed, 30 Sep 2026 06:00:00 GMT</pubDate>
        </item>
        <item>
          <title>Regional Flood Emergency Impacts University Examination Schedule</title>
          <link>https://news-beta.example.org/flood-warning-2</link>
          <description>Transport disruption and flood warnings reported near examination centers.</description>
          <pubDate>Wed, 30 Sep 2026 07:00:00 GMT</pubDate>
        </item>
      </channel>
    </rss>
    """
    parsed = adapter.parse_rss_or_atom_xml(rss_xml, default_publisher="Regional News Wire")
    assert len(parsed) == 2
    trends = adapter.detect_trends_and_related_groups(parsed)
    assert len(trends["top_trends"]) >= 1
    bundle = adapter._normalize_articles(parsed, collection_mode="rss_unit_test")
    assert len(bundle.events) == 2


def test_priority_5_web_crawler_structured_extraction():
    crawler = WebCrawlerAdapter(WebCrawlerConfig())
    sample_html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <title>Official Notice: Campus Water Supply Maintenance & Advisory</title>
        <meta name="author" content="Municipal Water Board" />
        <meta property="article:published_time" content="2026-09-30T05:30:00Z" />
        <meta name="description" content="Scheduled pipeline repair affecting institutional zone for 18 hours." />
    </head>
    <body>
        <script>alert('untrusted');</script>
        <article>
            <h1>Campus Water Supply Maintenance</h1>
            <p>Municipal authorities announced emergency pipeline repair affecting the institutional corridor.</p>
            <a href="https://municipal.example.gov/status">Live Status</a>
        </article>
    </body>
    </html>
    """
    doc = crawler.parse_html_metadata_and_content(
        "https://municipal.example.gov/notices/water-2026", sample_html
    )
    assert doc["title"] == "Official Notice: Campus Water Supply Maintenance & Advisory"
    assert doc["author"] == "Municipal Water Board"
    assert doc["language"] == "en"
    assert "alert(" not in doc["content"]
    assert "https://municipal.example.gov/status" in doc["relevant_links"]

    bundle = crawler.extract_from_html(
        "https://municipal.example.gov/notices/water-2026", sample_html
    )
    assert len(bundle.sources) == 1
    assert len(bundle.evidence) == 1
    assert len(bundle.events) == 1


# ==============================================================================
# 2. DEDUPLICATION, SOURCE INDEPENDENCE & CONFLICTING CLAIMS TESTS
# ==============================================================================


def test_deduplication_levels_and_syndicated_wire_non_inflation():
    now = datetime.now(timezone.utc)

    # Two sources that are syndicated copies of the exact same AP wire story
    src_wire_1 = OSINTSource(
        id="SRC_WIRE_1",
        name="Local Daily Portal A",
        type="trendradar_rss",
        url="https://portal-a.example.com/story",
        publisher="Local Daily Portal A",
        independence_group="ap_syndicate_group",
        is_official_source=False,
        collection_method="rss",
        reliability=0.75,
    )
    src_wire_2 = OSINTSource(
        id="SRC_WIRE_2",
        name="Regional Mirror Portal B",
        type="trendradar_rss",
        url="https://portal-b.example.com/story",
        publisher="Regional Mirror Portal B",
        independence_group="ap_syndicate_group",
        is_official_source=False,
        collection_method="rss",
        reliability=0.75,
    )
    src_official = OSINTSource(
        id="SRC_GOV_1",
        name="State Disaster Management Authority",
        type="official_government",
        url="https://sdma.example.gov.in/alert",
        publisher="SDMA",
        independence_group="sdma_official",
        is_official_source=True,
        collection_method="official_bulletin",
        reliability=0.95,
    )
    sources_map = {
        src_wire_1.id: src_wire_1,
        src_wire_2.id: src_wire_2,
        src_official.id: src_official,
    }

    ev_1 = OSINTEvent(
        id="EV_1",
        fingerprint="fp_substation_1",
        title="Substation fire disrupts northern academic sector power supply",
        description="Fire reported at 220kV substation serving northern sector.",
        event_type="infrastructure_disruption",
        first_observed=now,
        location=OSINTLocation(country="India", city="Ballari", latitude=15.14, longitude=76.92),
        severity="HIGH",
        confidence=0.75,
        source_ids=["SRC_WIRE_1"],
        origin_hashes=["SAME_WIRE_HASH_99"],
    )
    # Syndicated copy from second portal with identical wire origin hash
    ev_2 = OSINTEvent(
        id="EV_2",
        fingerprint="fp_substation_2",
        title="Substation fire disrupts northern academic sector power supply",
        description="Fire reported at 220kV substation serving northern sector.",
        event_type="infrastructure_disruption",
        first_observed=now + timedelta(minutes=10),
        location=OSINTLocation(country="India", city="Ballari", latitude=15.14, longitude=76.92),
        severity="HIGH",
        confidence=0.75,
        source_ids=["SRC_WIRE_2"],
        origin_hashes=["SAME_WIRE_HASH_99"],
    )

    deduped, conflicts = OSINTDeduplicationAndCorroborationEngine.process_events(
        [ev_1, ev_2], sources_by_id=sources_map
    )
    assert len(deduped) == 1
    # Because both share the same origin_hash & independence_group, independent_source_count MUST remain 1
    # and confidence MUST NOT be artificially boosted!
    assert deduped[0].independent_source_count == 1
    assert deduped[0].corroboration_status == "Single-source signal"
    assert deduped[0].confidence == 0.75

    # Now add an independent official government confirmation
    ev_3 = OSINTEvent(
        id="EV_3",
        fingerprint="fp_substation_3",
        title="Substation fire disrupts northern academic sector power supply",
        description="Official SDMA confirmation of fire at 220kV substation serving northern sector.",
        event_type="infrastructure_disruption",
        first_observed=now + timedelta(minutes=25),
        location=OSINTLocation(country="India", city="Ballari", latitude=15.14, longitude=76.92),
        severity="HIGH",
        confidence=0.85,
        source_ids=["SRC_GOV_1"],
        origin_hashes=["INDEPENDENT_GOV_HASH_100"],
    )
    deduped_2, _ = OSINTDeduplicationAndCorroborationEngine.process_events(
        [ev_1, ev_2, ev_3], sources_by_id=sources_map
    )
    assert len(deduped_2) == 1
    assert deduped_2[0].independent_source_count == 2
    assert deduped_2[0].corroboration_status == "Official-source confirmation"
    assert deduped_2[0].confidence > 0.75


def test_conflicting_source_claims_preserved_without_silent_overwrite():
    now = datetime.now(timezone.utc)
    ev_a = OSINTEvent(
        id="EV_CLAIM_A",
        fingerprint="fp_net_a",
        title="Campus network gateway outage reported by regional ISP",
        description="ISP reports severe fiber cut causing complete outage across campus ring.",
        event_type="cyber_threat",
        severity="CRITICAL",
        status="ACTIVE",
        first_observed=now,
        location=OSINTLocation(country="India", city="Ballari"),
        source_ids=["SRC_ISP_A"],
    )
    ev_b = OSINTEvent(
        id="EV_CLAIM_B",
        fingerprint="fp_net_b",
        title="Campus network gateway outage reported by regional ISP",
        description="No outage detected; backup routing fully restored and false alarm confirmed.",
        event_type="cyber_threat",
        severity="LOW",
        status="RESOLVED",
        first_observed=now + timedelta(minutes=20),
        location=OSINTLocation(country="India", city="Ballari"),
        source_ids=["SRC_NOC_B"],
    )
    conflict = OSINTDeduplicationAndCorroborationEngine.detect_claim_conflict(ev_a, ev_b)
    assert conflict is not None
    assert conflict.source_a_id == "SRC_ISP_A"
    assert conflict.source_b_id == "SRC_NOC_B"
    assert conflict.resolution_status == "UNRESOLVED"


# ==============================================================================
# 3. 5-STAGE CRISIS SIGNAL ENGINE & EPISTEMIC SEPARATION TESTS
# ==============================================================================


def test_5_stage_crisis_signal_ladder():
    e_obs = OSINTEvent(
        id="E1",
        fingerprint="fp1",
        title="Minor Notice",
        description="Low severity single source",
        severity="LOW",
        independent_source_count=1,
        corroboration_status="Single-source signal",
    )
    assert CrisisSignalEngine.determine_event_stage(e_obs, cluster_size=1) == "OBSERVATION"

    e_sig = OSINTEvent(
        id="E2",
        fingerprint="fp2",
        title="Moderate Alert",
        description="Medium severity single source",
        severity="MEDIUM",
        independent_source_count=1,
        corroboration_status="Single-source signal",
    )
    assert CrisisSignalEngine.determine_event_stage(e_sig, cluster_size=1) == "SIGNAL"

    e_dev = OSINTEvent(
        id="E3",
        fingerprint="fp3",
        title="High Uncorroborated Alert",
        description="High severity single source",
        severity="HIGH",
        independent_source_count=1,
        corroboration_status="Single-source signal",
    )
    assert CrisisSignalEngine.determine_event_stage(e_dev, cluster_size=1) == "DEVELOPING_EVENT"

    e_signif = OSINTEvent(
        id="E4",
        fingerprint="fp4",
        title="High Multi-Source Event",
        description="High severity corroborated",
        severity="HIGH",
        independent_source_count=2,
        corroboration_status="Multi-source corroborated signal",
    )
    assert CrisisSignalEngine.determine_event_stage(e_signif, cluster_size=1) == "SIGNIFICANT_EVENT"

    e_crisis = OSINTEvent(
        id="E5",
        fingerprint="fp5",
        title="Critical Official Crisis Candidate",
        description="Critical severity with official confirmation",
        severity="CRITICAL",
        independent_source_count=2,
        corroboration_status="Official-source confirmation",
    )
    assert CrisisSignalEngine.determine_event_stage(e_crisis, cluster_size=2) == "CRISIS_CANDIDATE"


# ==============================================================================
# 4. GEOSPATIAL HAVERSINE RADIUS & TEMPORAL KNOWLEDGE GRAPH TESTS
# ==============================================================================


def test_geospatial_haversine_radius_and_missing_coordinate_honesty():
    now = datetime.now(timezone.utc)
    ev_inside = OSINTEvent(
        id="EV_GEO_IN",
        fingerprint="fp_in",
        title="Substation Alert 12km Away",
        description="Verified coordinates within 100km.",
        event_type="infrastructure_disruption",
        severity="HIGH",
        first_observed=now - timedelta(hours=2),
        last_updated=now - timedelta(hours=2),
        location=OSINTLocation(
            country="India",
            city="Ballari",
            latitude=15.2000,
            longitude=76.9500,
            precision="exact_coordinates",
        ),
    )
    ev_outside = OSINTEvent(
        id="EV_GEO_OUT",
        fingerprint="fp_out",
        title="Distant Coastal Cyclone 600km Away",
        description="Outside 100km radius.",
        event_type="climate_environmental",
        severity="HIGH",
        first_observed=now - timedelta(hours=3),
        last_updated=now - timedelta(hours=3),
        location=OSINTLocation(
            country="India",
            city="Mumbai",
            latitude=19.0760,
            longitude=72.8777,
            precision="city_level",
        ),
    )
    ev_unlocated = OSINTEvent(
        id="EV_GEO_NONE",
        fingerprint="fp_none",
        title="National Cyber Advisory (No Coordinates)",
        description="No lat/lon coordinates.",
        event_type="cyber_threat",
        severity="MEDIUM",
        first_observed=now - timedelta(hours=1),
        last_updated=now - timedelta(hours=1),
        location=OSINTLocation(country="India", precision="country_level"),
    )

    req = GeofenceQueryRequest(
        latitude=15.1394,
        longitude=76.9214,
        radius_km=100.0,
        hours_back=24.0,
    )
    res = GeospatialIntelligenceEngine.query_geofence(
        query=req,
        events=[ev_inside, ev_outside, ev_unlocated],
        institutions=[
            {
                "id": "INST_1",
                "name": "Test Engineering Campus",
                "city": "Ballari",
                "state": "Karnataka",
                "latitude": 15.1400,
                "longitude": 76.9220,
            }
        ],
    )
    assert res.matching_event_count == 1
    assert res.events[0].id == "EV_GEO_IN"
    assert len(res.density_clusters) == 1
    assert len(res.nearby_infrastructure_and_institutions) == 1


def test_knowledge_graph_construction_and_traversal():
    src = OSINTSource(
        id="SRC_KG_1",
        name="CERT Threat Feed",
        type="opencti",
        publisher="CERT",
        collection_method="stix",
        reliability=0.95,
    )
    evid = OSINTEvidence(
        id="EVID_KG_1",
        source_id="SRC_KG_1",
        content_hash="abc123456789",
        extracted_content="Threat Actor APT-X targeted Regional University.",
        provenance=_make_provenance("EVID_KG_1", "SRC_KG_1"),
        extraction_method="stix",
    )
    actor = OSINTEntity(
        id="ENT_ACTOR_1",
        name="APT-X",
        entity_type="Threat Actor",
        confidence=0.9,
        evidence_ids=["EVID_KG_1"],
    )
    org = OSINTEntity(
        id="ENT_ORG_1",
        name="Regional University",
        entity_type="Organization",
        confidence=0.95,
        evidence_ids=["EVID_KG_1"],
    )
    rel = OSINTRelationship(
        id="REL_KG_1",
        source_entity="ENT_ACTOR_1",
        relationship_type="targets",
        target_entity="ENT_ORG_1",
        confidence=0.9,
        evidence=["EVID_KG_1"],
    )
    event = OSINTEvent(
        id="EV_KG_1",
        fingerprint="fp_kg_1",
        title="Targeted Intrusion Attempt on Regional University",
        description="APT-X targeted campus gateway.",
        event_type="cyber_threat",
        severity="HIGH",
        location=OSINTLocation(country="India", city="Ballari", latitude=15.1394, longitude=76.9214),
        entities=["ENT_ACTOR_1", "ENT_ORG_1"],
        source_ids=["SRC_KG_1"],
        evidence=[evid],
    )

    snapshot = OSINTKnowledgeGraphEngine.build_graph(
        sources=[src],
        evidence=[evid],
        entities=[actor, org],
        events=[event],
        relationships=[rel],
    )
    assert snapshot.total_nodes >= 6
    assert snapshot.total_edges >= 5
    rel_types = {e.relationship_type for e in snapshot.edges}
    assert "targets" in rel_types
    assert "affected-by" in rel_types
    assert "reported-by" in rel_types
    assert "supported-by" in rel_types

    sub = OSINTKnowledgeGraphEngine.filter_entity_subgraph(snapshot, "ENT_ACTOR_1")
    assert sub.total_nodes >= 2


# ==============================================================================
# 5. SECURITY, SSRF PROTECTION, XXE & ETHICAL OSINT GUARDRAILS TESTS
# ==============================================================================


def test_ssrf_protection_blocks_localhost_private_ips_and_cloud_metadata():
    valid = validate_external_url("https://8.8.8.8/dns-query", resolve_dns=False)
    assert valid.startswith("https://")

    with pytest.raises(SSRFViolationError):
        validate_external_url("http://127.0.0.1:8000/api/v1/admin")

    with pytest.raises(SSRFViolationError):
        validate_external_url("http://localhost/secret")

    with pytest.raises(SSRFViolationError):
        validate_external_url("http://169.254.169.254/latest/meta-data/")

    with pytest.raises(SSRFViolationError):
        validate_external_url("http://192.168.1.1/router")

    with pytest.raises(SSRFViolationError):
        validate_external_url("file:///etc/passwd")


def test_xml_xxe_blocked_and_unethical_osint_queries_rejected():
    xxe_payload = """<?xml version="1.0"?>
    <!DOCTYPE foo [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>
    <rss><channel><title>&xxe;</title></channel></rss>
    """
    with pytest.raises(ValueError, match="SECURITY_BLOCKED"):
        safe_parse_xml(xxe_payload)

    with pytest.raises(UnethicalOSINTRequestError):
        enforce_lawful_osint_query("credential dump leaked password database")


# ==============================================================================
# 6. FAILURE ISOLATION, 13 CONTROLLED MCP TOOLS & FASTAPI ENDPOINTS TESTS
# ==============================================================================


@pytest.mark.asyncio
async def test_provider_failure_isolation_and_13_controlled_mcp_tools():
    await init_db()
    orchestrator = OSINTIntelligenceOrchestrator()
    mcp_registry = ControlledMCPToolRegistry(orchestrator=orchestrator)
    tools = mcp_registry.list_tools()
    assert len(tools) == 13

    async with async_session_factory() as session:
        dummy_user = UserModel(
            id="USR_MCP_TEST",
            email="mcp@cip-test.org",
            role="Auditor",
            is_active=True,
        )
        # Unauthenticated call must return UNAUTHORIZED
        unauth_res = await mcp_registry.execute_tool(
            tool_name="search_global_intelligence",
            arguments={"query": "power"},
            session=session,
            current_user=None,
        )
        assert unauth_res.status == "UNAUTHORIZED"

        # Authenticated calls across the controlled MCP tools
        for tool_name, args in [
            ("search_global_intelligence", {"query": "grid"}),
            ("search_news", {"query": "karnataka"}),
            ("search_events", {"query": ""}),
            ("search_entities", {"query": ""}),
            ("search_threat_intelligence", {"query": ""}),
            ("search_opencti", {"query": ""}),
            ("search_misp", {"query": ""}),
            ("search_sources", {"query": ""}),
            ("search_evidence", {"query": ""}),
            ("get_event_timeline", {}),
            ("get_geofence_events", {"latitude": 15.1394, "longitude": 76.9214, "radius_km": 100}),
            ("get_entity_relationships", {"entity_id": "OpenCTI"}),
            ("generate_situation_brief", {"scope": "Test Brief", "use_live_llm": False}),
        ]:
            res = await mcp_registry.execute_tool(
                tool_name=tool_name,
                arguments=args,
                session=session,
                current_user=dummy_user,
            )
            assert res.status == "SUCCESS", f"MCP tool {tool_name} failed: {res.error_detail}"


def test_fastapi_osint_endpoints_and_dashboard_ui():
    with TestClient(app) as client:
        # 1. Unauthenticated requests to /api/v1/osint/* must return 401
        unauth = client.get("/api/v1/osint/health")
        assert unauth.status_code == 401

        # 2. Authenticate via signup
        email = f"osint_tester_{int(datetime.now(timezone.utc).timestamp())}@cip-test.org"
        signup_res = client.post(
            "/api/v1/auth/signup",
            json={
                "email": email,
                "password": "StrongPassword!2026",
                "full_name": "OSINT Crisis Analyst",
                "entity_category": "educational_institution",
                "entity_type": "University",
                "institution_name": "OSINT Verification University",
            },
        )
        assert signup_res.status_code in (200, 201)
        token = signup_res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 3. Trigger multi-provider OSINT collection via API
        collect_res = client.post(
            "/api/v1/osint/collect",
            headers=headers,
            json={"query": "institutional infrastructure"},
        )
        assert collect_res.status_code == 200
        assert collect_res.json()["status"] == "COLLECTION_COMPLETED"
        assert len(collect_res.json()["pipeline_stages_completed"]) == 15

        # 4. Verify all GET OSINT endpoints
        for endpoint in [
            "/api/v1/osint/health",
            "/api/v1/osint/dashboard",
            "/api/v1/osint/events",
            "/api/v1/osint/sources",
            "/api/v1/osint/evidence",
            "/api/v1/osint/entities",
            "/api/v1/osint/threat-intelligence",
            "/api/v1/osint/timeline",
            "/api/v1/osint/knowledge-graph",
            "/api/v1/osint/mcp/tools",
        ]:
            r = client.get(endpoint, headers=headers)
            assert r.status_code == 200, f"Failed on {endpoint}: {r.text}"

        # 5. Geofence query endpoint
        geo_res = client.post(
            "/api/v1/osint/geofence",
            headers=headers,
            json={
                "latitude": 15.1394,
                "longitude": 76.9214,
                "radius_km": 250.0,
                "hours_back": 72.0,
            },
        )
        assert geo_res.status_code == 200
        assert "matching_event_count" in geo_res.json()

        # 6. Situation Brief endpoint
        brief_res = client.post(
            "/api/v1/osint/briefs",
            headers=headers,
            json={"scope": "Ballari Institutional Corridor", "use_live_llm": False},
        )
        assert brief_res.status_code == 200
        brief = brief_res.json()
        assert "executive_summary" in brief
        assert "key_extracted_facts" in brief
        assert "supporting_evidence" in brief

        # 7. SSRF-protected web extraction endpoint blocks localhost and allows valid HTML extraction
        ssrf_block_res = client.post(
            "/api/v1/osint/web-extract",
            headers=headers,
            json={"url": "http://127.0.0.1:8000/admin"},
        )
        assert ssrf_block_res.status_code == 400

        valid_extract_res = client.post(
            "/api/v1/osint/web-extract",
            headers=headers,
            json={
                "url": "https://8.8.8.8/advisory-notice",
                "html_content": "<html><head><title>Grid Notice</title></head><body><p>Substation maintenance scheduled.</p></body></html>",
            },
        )
        assert valid_extract_res.status_code == 200
        assert valid_extract_res.json()["status"] == "EXTRACTED"

        # 8. Dashboard UI includes Workflow 11 (view-osint)
        dash_res = client.get("/dashboard/")
        assert dash_res.status_code == 200
        assert 'id="view-osint"' in dash_res.text
        assert 'id="nav-osint"' in dash_res.text
