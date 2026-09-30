"""
PRIORITY 1 — WORLD INTEL MCP Adapter (src/integrations/world_intel/adapter.py).

Wraps global intelligence capabilities inspired by https://github.com/marc-shade/world-intel-mcp:
- geopolitical information
- conflict & military information
- cyber intelligence
- climate & environmental information
- supply-chain signals
- geofenced monitoring
- situation briefs

Maintains strict 7-field provenance, 5-tier epistemic separation, SSRF protection,
TTL/content-hash caching, and fault-isolated fallback when external MCP server is absent.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from typing import Any, Dict, List, Optional
import httpx

from src.contracts.evidence_investigation import ProvenanceRecord
from src.contracts.osint_intelligence import (
    EpistemicClaimSeparation,
    OSINTEntity,
    OSINTEvent,
    OSINTEvidence,
    OSINTLocation,
    OSINTRelationship,
    OSINTSource,
)
from src.integrations.base import BaseOSINTProvider, OSINTResponseCache, ProviderIngestionBundle
from src.security.ssrf_guard import (
    compute_content_hash,
    enforce_lawful_osint_query,
    sanitize_untrusted_text,
    validate_external_url,
)


@dataclass
class WorldIntelConfig:
    endpoint_url: str = ""
    api_key: str = ""
    timeout_seconds: float = 10.0

    @classmethod
    def from_env(cls) -> "WorldIntelConfig":
        return cls(
            endpoint_url=os.getenv("WORLD_INTEL_MCP_URL", "").strip(),
            api_key=os.getenv("WORLD_INTEL_API_KEY", "").strip(),
            timeout_seconds=float(os.getenv("WORLD_INTEL_TIMEOUT_SECONDS", "10.0")),
        )


class WorldIntelMCPAdapter(BaseOSINTProvider):
    """
    AI-CRISS Adapter for World Intel MCP (Priority 1).
    Exposes global, geopolitical, conflict, military, cyber, climate, and supply-chain signals.
    """

    provider_id = "world_intel"
    display_name = "WORLD INTEL MCP"
    priority = 1
    capabilities = [
        "global_intelligence",
        "geopolitical_monitoring",
        "conflict_and_military_signals",
        "cyber_intelligence",
        "climate_and_environmental_alerts",
        "supply_chain_disruption_signals",
        "geofenced_crisis_monitoring",
        "situation_brief_inputs",
    ]

    def __init__(
        self,
        config: Optional[WorldIntelConfig] = None,
        cache: Optional[OSINTResponseCache] = None,
    ):
        super().__init__(cache=cache)
        self.config = config or WorldIntelConfig.from_env()

    def is_configured(self) -> bool:
        return bool(self.config.endpoint_url)

    def _normalize_raw_items(self, raw_items: List[Dict[str, Any]], collection_mode: str) -> ProviderIngestionBundle:
        now = datetime.now(timezone.utc)
        src = OSINTSource(
            id="src_world_intel_mcp",
            name="World Intel MCP Global Feed",
            type="world_intel_mcp",
            url=self.config.endpoint_url or "mcp://world-intel-mcp/global-signals",
            publisher="World Intel MCP Aggregator",
            independence_group="world_intel_primary",
            is_official_source=False,
            collection_method=collection_mode,
            reliability=0.86,
            timestamp=now,
            license_metadata={"provider": "world-intel-mcp", "adapter_mode": collection_mode},
        )

        evidence_list: List[OSINTEvidence] = []
        entity_list: List[OSINTEntity] = []
        event_list: List[OSINTEvent] = []
        rel_list: List[OSINTRelationship] = []

        for idx, item in enumerate(raw_items):
            title = sanitize_untrusted_text(str(item.get("title") or f"Global Signal #{idx + 1}"), 500)
            summary = sanitize_untrusted_text(str(item.get("summary") or item.get("description") or title), 4000)
            domain = str(item.get("category") or "geopolitical").lower()
            event_type_map = {
                "geopolitical": "geopolitical",
                "conflict": "conflict",
                "military": "military",
                "cyber": "cyber_threat",
                "climate": "climate_environmental",
                "supply_chain": "supply_chain",
                "infrastructure": "infrastructure_disruption",
            }
            ev_type = event_type_map.get(domain, "geopolitical")
            sev = str(item.get("severity") or "HIGH").upper()
            if sev not in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
                sev = "MEDIUM"

            lat = item.get("latitude")
            lon = item.get("longitude")
            country = item.get("country") or "India"
            region = item.get("region") or "Karnataka"
            city = item.get("city") or "Ballari"

            loc = OSINTLocation(
                latitude=float(lat) if lat is not None else None,
                longitude=float(lon) if lon is not None else None,
                country=str(country),
                region=str(region),
                city=str(city),
                precision="exact_coordinates" if (lat is not None and lon is not None) else "city_level",
            )

            c_hash = compute_content_hash(f"{title}:{summary}:{country}:{city}")
            ev_id = f"ev_wi_{c_hash[:12]}"
            evt_id = str(item.get("id") or f"evt_wi_{c_hash[:12]}")
            pub_ts = now - timedelta(hours=float(item.get("hours_ago", 2.0)))

            prov = ProvenanceRecord(
                evidence_id=ev_id,
                source=src.id,
                document=str(item.get("source_url") or src.url),
                page_or_section=f"WorldIntel:{domain.upper()}",
                table_cell_or_range=f"Signal[{idx}]",
                excerpt_or_image=summary[:400],
                extraction_confidence=float(item.get("confidence", 0.88)),
                date_or_context=f"{pub_ts.isoformat()} ({city}, {country})",
                domain=ev_type,
                metric_name=domain,
            )

            ev_obj = OSINTEvidence(
                id=ev_id,
                source_id=src.id,
                source_url=str(item.get("source_url") or src.url),
                content_hash=c_hash,
                extracted_content=summary,
                captured_at=now,
                published_at=pub_ts,
                provenance=prov,
                confidence=float(item.get("confidence", 0.88)),
                extraction_method=f"WorldIntelMCPAdapter ({collection_mode})",
                title=title,
            )
            evidence_list.append(ev_obj)

            ent_ids: List[str] = []
            for ent_raw in item.get("entities", []):
                ent_name = sanitize_untrusted_text(str(ent_raw.get("name") or "Unknown Entity"), 200)
                ent_type = ent_raw.get("type") or "Organization"
                ent_id = f"ent_wi_{compute_content_hash(f'{ent_type}:{ent_name}')[:10]}"
                ent_obj = OSINTEntity(
                    id=ent_id,
                    name=ent_name,
                    entity_type=ent_type,  # type: ignore[arg-type]
                    confidence=float(item.get("confidence", 0.85)),
                    first_seen=pub_ts,
                    last_seen=now,
                    location=loc,
                    source_references=[src.id],
                    evidence_ids=[ev_id],
                )
                entity_list.append(ent_obj)
                ent_ids.append(ent_id)

            loc_ent_id = f"ent_loc_{compute_content_hash(f'{country}:{city}')[:10]}"
            entity_list.append(
                OSINTEntity(
                    id=loc_ent_id,
                    name=f"{city}, {country}",
                    entity_type="Location",
                    confidence=0.95,
                    first_seen=pub_ts,
                    last_seen=now,
                    location=loc,
                    source_references=[src.id],
                    evidence_ids=[ev_id],
                )
            )
            for eid in ent_ids:
                rel_list.append(
                    OSINTRelationship(
                        id=f"rel_{eid}_{loc_ent_id}",
                        source_entity=eid,
                        relationship_type="located-in",
                        target_entity=loc_ent_id,
                        confidence=0.9,
                        evidence=[ev_id],
                        timestamp=now,
                    )
                )

            fp = compute_content_hash(f"{ev_type}:{country.lower()}:{city.lower()}:{title.lower()[:48]}")
            evt = OSINTEvent(
                id=evt_id,
                fingerprint=fp,
                title=title,
                description=summary,
                event_type=ev_type,  # type: ignore[arg-type]
                start_time=pub_ts,
                first_observed=pub_ts,
                reported_at=pub_ts,
                last_updated=now,
                location=loc,
                entities=ent_ids + [loc_ent_id],
                severity=sev,  # type: ignore[arg-type]
                confidence=float(item.get("confidence", 0.86)),
                source_count=1,
                independent_source_count=1,
                source_ids=[src.id],
                origin_hashes=[c_hash],
                evidence=[ev_obj],
                status="ACTIVE",
                epistemic_chain=EpistemicClaimSeparation(
                    raw_source=[f"[{src.id}] {summary}"],
                    extracted_facts=[
                        f"Category '{ev_type}' event observed in {city}, {country} at {pub_ts.isoformat()} (severity={sev})."
                    ],
                    correlated_facts=[],
                    ai_interpretation=[],
                    ai_assessment=None,
                ),
            )
            event_list.append(evt)

        return ProviderIngestionBundle(
            provider_id=self.provider_id,
            sources=[src],
            evidence=evidence_list,
            entities=entity_list,
            events=event_list,
            relationships=rel_list,
        )

    def _deterministic_reference_signals(self, query: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Deterministic reference global intelligence signals used when live WORLD_INTEL_MCP_URL
        is unconfigured or unreachable in local/test environments (Section 36).
        """
        catalog = [
            {
                "id": "evt_wi_grid_substation_01",
                "title": "Regional Power Grid & Telecommunications Substation Disruption Near Ballari Corridor",
                "summary": "Public utility telemetry and regional situational monitoring indicate intermittent 220kV feeder instability and fiber backhaul degradation affecting educational and industrial campuses within the Ballari-Hosapete corridor.",
                "category": "infrastructure",
                "severity": "HIGH",
                "latitude": 15.1394,
                "longitude": 76.9214,
                "country": "India",
                "region": "Karnataka",
                "city": "Ballari",
                "hours_ago": 3.5,
                "confidence": 0.89,
                "source_url": "https://world-intel.reference.local/signals/grid-substation-ballari-2026",
                "entities": [
                    {"name": "Ballari 220kV Grid Corridor", "type": "Infrastructure"},
                    {"name": "Karnataka Regional Academic Network", "type": "Organization"},
                ],
            },
            {
                "id": "evt_wi_supply_chain_semicon_02",
                "title": "South Asia Campus IT & Lab Equipment Supply-Chain Transit Delay",
                "summary": "Maritime and customs logistics monitoring report a 14-day transit bottleneck affecting laboratory instrumentation and server hardware deliveries across Southern India academic and research hubs.",
                "category": "supply_chain",
                "severity": "MEDIUM",
                "latitude": 12.9716,
                "longitude": 77.5946,
                "country": "India",
                "region": "Karnataka",
                "city": "Bengaluru",
                "hours_ago": 8.0,
                "confidence": 0.84,
                "source_url": "https://world-intel.reference.local/signals/supply-chain-south-asia-2026",
                "entities": [
                    {"name": "Southern Corridor Logistics Hub", "type": "Infrastructure"},
                ],
            },
            {
                "id": "evt_wi_climate_monsoon_03",
                "title": "Severe Hydro-Meteorological Flash Flood Advisory for Northern Karnataka Districts",
                "summary": "Meteorological and satellite hydrological sensors flag elevated flash-flood and transport disruption risk across Northern and Central Karnataka over the next 48 hours.",
                "category": "climate",
                "severity": "HIGH",
                "latitude": 15.3647,
                "longitude": 75.1240,
                "country": "India",
                "region": "Karnataka",
                "city": "Hubballi",
                "hours_ago": 5.0,
                "confidence": 0.91,
                "source_url": "https://world-intel.reference.local/signals/climate-karnataka-advisory-2026",
                "entities": [
                    {"name": "Northern Karnataka Transit Network", "type": "Infrastructure"},
                ],
            },
        ]
        if query:
            q_low = query.lower()
            filtered = [
                item for item in catalog
                if q_low in item["title"].lower()
                or q_low in item["summary"].lower()
                or q_low in item["category"].lower()
                or q_low in item["city"].lower()
                or q_low in item["country"].lower()
            ]
            return filtered if filtered else catalog
        return catalog

    def collect_intelligence(self, query: Optional[str] = None, **kwargs: Any) -> ProviderIngestionBundle:
        enforce_lawful_osint_query(query or "")
        cache_key = self.cache.make_key(self.provider_id, "collect", {"query": query or "", **kwargs})
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        def _primary() -> ProviderIngestionBundle:
            if not self.is_configured():
                items = self._deterministic_reference_signals(query=query)
                bundle = self._normalize_raw_items(items, collection_mode="deterministic_reference_mcp")
                bundle.used_fallback_or_cache = True
                self.cache.set(cache_key, bundle)
                return bundle

            safe_url = validate_external_url(self.config.endpoint_url)
            headers: Dict[str, str] = {"Accept": "application/json"}
            if self.config.api_key:
                headers["Authorization"] = f"Bearer {self.config.api_key}"

            with httpx.Client(timeout=self.config.timeout_seconds) as client:
                resp = client.get(safe_url, params={"q": query or ""}, headers=headers)
                if resp.status_code != 200:
                    raise RuntimeError(f"World Intel MCP returned HTTP {resp.status_code}")
                data = resp.json()
                raw_items = data.get("signals") or data.get("events") or (data if isinstance(data, list) else [])

            bundle = self._normalize_raw_items(raw_items, collection_mode="live_mcp_http")
            self.cache.set(cache_key, bundle, etag=resp.headers.get("ETag"))
            return bundle

        def _fallback(err_msg: str) -> ProviderIngestionBundle:
            items = self._deterministic_reference_signals(query=query)
            return self._normalize_raw_items(items, collection_mode="fault_isolated_reference_mcp")

        return self.execute_fault_isolated(
            operation_name="collect_world_intel",
            primary_fn=_primary,
            fallback_fn=_fallback,
            secrets_to_redact=[self.config.api_key] if self.config.api_key else [],
        )
