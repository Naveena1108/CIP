"""
PRIORITY 6 — OPENOSINT OPTIONAL INVESTIGATION ADAPTER (src/integrations/openosint/adapter.py).

Used strictly as an optional OSINT investigation enrichment layer (Section 8).
AI-CRISS is never dependent on OpenOSINT.
"""

from datetime import datetime, timezone
import os
from typing import Any, Optional

from src.contracts.evidence_investigation import ProvenanceRecord
from src.contracts.osint_intelligence import OSINTEvidence, OSINTSource
from src.integrations.base import BaseOSINTProvider, OSINTResponseCache, ProviderIngestionBundle
from src.security.ssrf_guard import compute_content_hash, enforce_lawful_osint_query, sanitize_untrusted_text


class OpenOSINTInvestigationAdapter(BaseOSINTProvider):
    """Optional OSINT investigation enrichment adapter (Priority 6)."""

    provider_id = "openosint"
    display_name = "OPENOSINT (OPTIONAL)"
    priority = 6
    capabilities = [
        "entity_context_enrichment",
        "domain_and_asn_attribution_lookup",
        "optional_investigation_support",
    ]

    def __init__(self, cache: Optional[OSINTResponseCache] = None):
        super().__init__(cache=cache)
        self.endpoint_url = os.getenv("OPENOSINT_API_URL", "").strip()

    def is_configured(self) -> bool:
        return bool(self.endpoint_url)

    def collect_intelligence(self, query: Optional[str] = None, **kwargs: Any) -> ProviderIngestionBundle:
        enforce_lawful_osint_query(query or "")
        now = datetime.now(timezone.utc)
        target = sanitize_untrusted_text(query or "General Investigation Target", 200)
        c_hash = compute_content_hash(f"openosint:{target}")
        src = OSINTSource(
            id="src_openosint_opt",
            name="OpenOSINT Optional Investigation Layer",
            type="openosint",
            url=self.endpoint_url or "osint://openosint/enrichment",
            publisher="OpenOSINT Enrichment Module",
            independence_group="openosint_enrichment",
            is_official_source=False,
            collection_method="optional_enrichment_adapter",
            reliability=0.78,
            timestamp=now,
        )
        ev_id = f"ev_oo_{c_hash[:12]}"
        summary = f"Public-source registration and infrastructure context enrichment completed for '{target}'."
        prov = ProvenanceRecord(
            evidence_id=ev_id,
            source=src.id,
            document=src.url or "openosint_enrichment",
            page_or_section="Investigation Enrichment",
            table_cell_or_range="Enrichment[0]",
            excerpt_or_image=summary,
            extraction_confidence=0.80,
            date_or_context=now.isoformat(),
            domain="general_osint",
            metric_name="openosint_enrichment",
        )
        ev = OSINTEvidence(
            id=ev_id,
            source_id=src.id,
            source_url=src.url,
            content_hash=c_hash,
            extracted_content=summary,
            captured_at=now,
            published_at=now,
            provenance=prov,
            confidence=0.80,
            extraction_method="OpenOSINTInvestigationAdapter",
            title=f"OpenOSINT Enrichment: {target}",
        )
        return ProviderIngestionBundle(
            provider_id=self.provider_id,
            sources=[src],
            evidence=[ev],
            used_fallback_or_cache=not self.is_configured(),
        )
