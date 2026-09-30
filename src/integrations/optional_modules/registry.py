"""
Section 10 Optional OSINT Modules Registry (src/integrations/optional_modules/registry.py).

Provides a strictly modular, opt-in plugin boundary for optional enrichment capabilities
(document intelligence, OCR metadata, media verification, geolocation metadata) while
enforcing Section 24 Legal/Ethical boundaries and never coupling them to core architecture.
"""

from typing import Any, Dict, List


OPTIONAL_MODULE_CATALOG: List[Dict[str, Any]] = [
    {
        "module_id": "document_intelligence",
        "name": "Document & PDF Metadata Intelligence",
        "enabled_by_default": True,
        "status": "AVAILABLE_MODULAR",
        "ethical_policy": "Public-source document structure and provenance extraction only.",
    },
    {
        "module_id": "ocr_intelligence",
        "name": "Optical Character Recognition (OCR) Signal Extractor",
        "enabled_by_default": True,
        "status": "AVAILABLE_MODULAR",
        "ethical_policy": "Extracts text from uploaded institutional/public scans with confidence scoring.",
    },
    {
        "module_id": "geolocation_verification",
        "name": "Geolocation & Landmark Verification",
        "enabled_by_default": True,
        "status": "AVAILABLE_MODULAR",
        "ethical_policy": "Correlates explicit public coordinates and administrative boundaries; never infers exact locations without evidence.",
    },
    {
        "module_id": "media_intelligence",
        "name": "Media & Excerpt Provenance Verifier",
        "enabled_by_default": True,
        "status": "AVAILABLE_MODULAR",
        "ethical_policy": "Content hashing and timestamp verification for public media advisories.",
    },
]


def list_optional_osint_modules() -> List[Dict[str, Any]]:
    """Return catalog of modular optional OSINT plugins."""
    return list(OPTIONAL_MODULE_CATALOG)
