"""
PRIORITY 3 — MISP INTEGRATION ADAPTER (src/integrations/misp/adapter.py).

Implements the Section 5 architecture:
  MISP -> OpenCTI connector -> OpenCTI -> AI-CRISS intelligence adapter

Does NOT implement a duplicate MISP-to-OpenCTI synchronization engine.
Only provides a direct MISP Event JSON -> STIX 2.1 bridge for the concrete capability of
inspecting standalone/air-gapped MISP JSON exports when an OpenCTI instance is not bridging them.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from typing import Any, Dict, List, Optional

from src.integrations.base import BaseOSINTProvider, OSINTResponseCache, ProviderIngestionBundle
from src.integrations.opencti.adapter import OpenCTIQueryAdapter
from src.security.ssrf_guard import enforce_lawful_osint_query, sanitize_untrusted_text


@dataclass
class MISPConfig:
    use_opencti_connector: bool = True
    direct_misp_url: str = ""
    direct_misp_key: str = ""

    @classmethod
    def from_env(cls) -> "MISPConfig":
        return cls(
            use_opencti_connector=os.getenv("MISP_USE_OPENCTI_CONNECTOR", "true").lower() != "false",
            direct_misp_url=os.getenv("MISP_DIRECT_URL", "").strip(),
            direct_misp_key=os.getenv("MISP_DIRECT_API_KEY", "").strip(),
        )


class MISPIntegrationAdapter(BaseOSINTProvider):
    """
    AI-CRISS MISP Adapter (Priority 3).
    Delegates primary MISP synchronization to the OpenCTI MISP connector (`MISP -> OpenCTI -> AI-CRISS`)
    and provides a lightweight MISP Event JSON -> STIX 2.1 converter only for standalone MISP event payloads.
    """

    provider_id = "misp"
    display_name = "MISP"
    priority = 3
    capabilities = [
        "opencti_misp_connector_bridge",
        "standalone_misp_event_json_normalization",
        "ioc_attribute_extraction",
    ]

    def __init__(
        self,
        config: Optional[MISPConfig] = None,
        opencti_adapter: Optional[OpenCTIQueryAdapter] = None,
        cache: Optional[OSINTResponseCache] = None,
    ):
        super().__init__(cache=cache)
        self.config = config or MISPConfig.from_env()
        self.opencti_adapter = opencti_adapter or OpenCTIQueryAdapter(cache=self.cache)

    def is_configured(self) -> bool:
        return bool(self.config.use_opencti_connector or self.opencti_adapter.is_configured() or self.config.direct_misp_url)

    def convert_standalone_misp_event_to_stix_bundle(self, misp_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Concrete fallback capability: convert an offline/standalone MISP Event JSON structure
        into a STIX 2.1 bundle so it flows through the unified OpenCTI STIX mapper without
        duplicating entity/relationship persistence logic.
        """
        event_obj = misp_payload.get("Event") or misp_payload
        event_uuid = str(event_obj.get("uuid") or event_obj.get("id") or "misp-standalone-01")
        info = sanitize_untrusted_text(str(event_obj.get("info") or "MISP Threat Event"), 400)
        now_iso = datetime.now(timezone.utc).isoformat()

        stix_objects: List[Dict[str, Any]] = [
            {
                "type": "report",
                "id": f"report--misp-{event_uuid}",
                "name": info,
                "description": f"Normalized from MISP Event {event_uuid}: {info}",
                "confidence": 88,
                "published": now_iso,
                "external_references": [{"source_name": "MISP", "external_id": event_uuid}],
            }
        ]

        for idx, attr in enumerate(event_obj.get("Attribute") or []):
            attr_type = str(attr.get("type") or "ip-dst")
            val = sanitize_untrusted_text(str(attr.get("value") or ""), 255)
            if not val:
                continue
            ind_id = f"indicator--misp-{event_uuid}-{idx}"
            stix_objects.append(
                {
                    "type": "indicator",
                    "id": ind_id,
                    "name": f"MISP Attribute ({attr_type}): {val}",
                    "description": sanitize_untrusted_text(str(attr.get("comment") or info), 500),
                    "pattern": f"[{attr_type}:value = '{val}']",
                    "pattern_type": "misp-attribute",
                    "observable_value": val,
                    "x_misp_event_uuid": event_uuid,
                    "confidence": 90 if attr.get("to_ids", True) else 70,
                    "first_seen": now_iso,
                    "external_references": [{"source_name": "MISP", "external_id": event_uuid}],
                }
            )
            stix_objects.append(
                {
                    "type": "relationship",
                    "source_ref": ind_id,
                    "relationship_type": "indicates",
                    "target_ref": f"report--misp-{event_uuid}",
                    "confidence": 88,
                }
            )

        return {"type": "bundle", "id": f"bundle--misp-{event_uuid}", "objects": stix_objects}

    def collect_intelligence(self, query: Optional[str] = None, **kwargs: Any) -> ProviderIngestionBundle:
        enforce_lawful_osint_query(query or "")

        def _primary() -> ProviderIngestionBundle:
            standalone_misp = kwargs.get("misp_event_json")
            if standalone_misp and isinstance(standalone_misp, dict):
                stix_bundle = self.convert_standalone_misp_event_to_stix_bundle(standalone_misp)
                bundle = self.opencti_adapter.ingest_stix_bundle(
                    stix_bundle,
                    collection_mode="misp_standalone_to_stix_bridge",
                )
                bundle.provider_id = self.provider_id
                for s in bundle.sources:
                    s.id = "src_misp_via_opencti"
                    s.name = "MISP Threat Sharing (via OpenCTI STIX Bridge)"
                    s.type = "misp"
                return bundle

            # Standard Section 5 path: MISP -> OpenCTI Connector -> OpenCTI -> AI-CRISS
            octi_bundle = self.opencti_adapter.collect_intelligence(query=query)
            misp_entities = [
                e for e in octi_bundle.entities
                if e.attributes.get("misp_event_uuid")
                or (not query)
                or (query.lower() in e.name.lower())
            ]
            return ProviderIngestionBundle(
                provider_id=self.provider_id,
                sources=octi_bundle.sources,
                evidence=octi_bundle.evidence,
                entities=misp_entities,
                events=octi_bundle.events,
                relationships=octi_bundle.relationships,
                used_fallback_or_cache=octi_bundle.used_fallback_or_cache,
            )

        return self.execute_fault_isolated(
            operation_name="collect_misp_via_opencti",
            primary_fn=_primary,
            fallback_fn=None,
            secrets_to_redact=[self.config.direct_misp_key] if self.config.direct_misp_key else [],
        )
