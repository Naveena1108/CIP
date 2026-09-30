from typing import Dict, List, Any
from src.adapters.base_adapter import BaseSignalAdapter
from src.contracts import (
    ProvenanceMetadata,
    CETRankingSignal,
    AdmissionsSignal,
    PlacementsSignal
)

class JSONDictionaryAdapter(BaseSignalAdapter):
    """
    Ingestion Adapter: Converts raw dictionaries / JSON payloads
    into validated Canonical Contracts with provenance.
    """
    def __init__(self, source_id: str = "JSON_RAW_EXPORT", source_type: str = "synthetic_generated"):
        self.source_id = source_id
        self.source_type = source_type

    def parse(self, raw_data: Dict[str, Any]) -> Dict[str, List[Any]]:
        provenance = ProvenanceMetadata(
            source_id=self.source_id,
            source_type=self.source_type
        )
        
        cet_signals = [
            CETRankingSignal(**item, provenance=provenance)
            for item in raw_data.get("cet_ranking", [])
        ]
        adm_signals = [
            AdmissionsSignal(**item, provenance=provenance)
            for item in raw_data.get("admissions", [])
        ]
        plc_signals = [
            PlacementsSignal(**item, provenance=provenance)
            for item in raw_data.get("placements", [])
        ]

        return {
            "cet_ranking": cet_signals,
            "admissions": adm_signals,
            "placements": plc_signals
        }
