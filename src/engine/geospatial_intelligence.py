"""
AI-CRISS (CIP) — Geospatial Intelligence, Geofencing & Priority 7 World Monitor Engine
(src/engine/geospatial_intelligence.py).

Implements Section 17 & Section 9 capabilities:
- Haversine great-circle distance calculation
- Geofenced radius + temporal window queries:
  "What significant events occurred within R km of this location during the last T hours?"
- Event density & geographic clustering
- Nearby infrastructure & institutional asset correlation
- Strict honesty: NEVER infers exact coordinates when an event lacks coordinate evidence.
"""

from datetime import datetime, timedelta, timezone
import math
from typing import Any, Dict, List, Optional

from src.contracts.osint_intelligence import (
    CrisisSignalStage,
    GeofenceCluster,
    GeofenceIntelligenceResponse,
    GeofenceQueryRequest,
    OSINTEntity,
    OSINTEvent,
    OSINTSeverity,
)
from src.engine.crisis_signal_engine import SEVERITY_RANK, STAGE_ORDER
from src.security.ssrf_guard import compute_content_hash


EARTH_RADIUS_KM = 6371.0088

# Known reference coordinates for registered Karnataka cities (used only for institutional asset lookup when city is known)
KNOWN_CITY_COORDINATES = {
    "ballari": (15.1394, 76.9214),
    "bellary": (15.1394, 76.9214),
    "bengaluru": (12.9716, 77.5946),
    "bangalore": (12.9716, 77.5946),
    "hubballi": (15.3647, 75.1240),
    "dharwad": (15.4589, 75.0078),
    "mysuru": (12.2958, 76.6394),
}


def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute exact Haversine distance in kilometers between two WGS84 coordinates."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = (math.sin(d_phi / 2.0) ** 2) + math.cos(phi1) * math.cos(phi2) * (math.sin(d_lambda / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return round(EARTH_RADIUS_KM * c, 2)


class GeospatialIntelligenceEngine:
    """Geospatial radius filtering, density clustering, and nearby infrastructure correlation."""

    @classmethod
    def query_geofence(
        cls,
        query: GeofenceQueryRequest,
        events: List[OSINTEvent],
        entities: Optional[List[OSINTEntity]] = None,
        institutions: Optional[List[Dict[str, Any]]] = None,
    ) -> GeofenceIntelligenceResponse:
        """
        Answer geospatial intelligence queries such as:
        'What significant events occurred within 100 km of this location during the last 24 hours?'
        Never fabricates coordinates for events that lack latitude/longitude evidence.
        """
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=query.hours_back)
        min_sev_rank = SEVERITY_RANK.get(query.min_severity, 1) if query.min_severity else 1

        matched_events: List[OSINTEvent] = []
        distances_km: Dict[str, float] = {}

        for evt in events:
            # Must have explicit coordinate evidence — never infer exact locations without evidence!
            if evt.location.latitude is None or evt.location.longitude is None:
                continue

            evt_time = evt.last_updated or evt.first_observed
            if evt_time < cutoff:
                continue

            if SEVERITY_RANK.get(evt.severity, 1) < min_sev_rank:
                continue

            dist = haversine_distance_km(
                query.latitude,
                query.longitude,
                evt.location.latitude,
                evt.location.longitude,
            )
            if dist <= query.radius_km:
                matched_events.append(evt)
                distances_km[evt.id] = dist

        matched_events.sort(key=lambda e: distances_km.get(e.id, 999999.0))

        # Nearby infrastructure & institutions within the geofence
        nearby_assets: List[Dict[str, Any]] = []
        for ent in (entities or []):
            if ent.entity_type in ("Infrastructure", "Organization", "Asset") and ent.location:
                if ent.location.latitude is not None and ent.location.longitude is not None:
                    d_ent = haversine_distance_km(
                        query.latitude,
                        query.longitude,
                        ent.location.latitude,
                        ent.location.longitude,
                    )
                    if d_ent <= query.radius_km:
                        nearby_assets.append(
                            {
                                "id": ent.id,
                                "name": ent.name,
                                "type": ent.entity_type,
                                "distance_km": d_ent,
                                "city": ent.location.city,
                                "country": ent.location.country,
                            }
                        )

        for inst in (institutions or []):
            ilat = inst.get("latitude")
            ilon = inst.get("longitude")
            if ilat is None or ilon is None:
                city_str = str(inst.get("city") or "").strip().lower()
                if city_str in KNOWN_CITY_COORDINATES:
                    ilat, ilon = KNOWN_CITY_COORDINATES[city_str]
            if ilat is not None and ilon is not None:
                d_inst = haversine_distance_km(query.latitude, query.longitude, float(ilat), float(ilon))
                if d_inst <= query.radius_km:
                    nearby_assets.append(
                        {
                            "id": inst.get("id"),
                            "name": inst.get("name"),
                            "type": "Registered Institution",
                            "distance_km": d_inst,
                            "city": inst.get("city"),
                            "country": inst.get("state") or "India",
                        }
                    )

        clusters = cls.compute_density_clusters(matched_events, nearby_assets)

        if not matched_events:
            summary = (
                f"No coordinate-verified events occurred within {query.radius_km:.0f} km of "
                f"({query.latitude:.4f}, {query.longitude:.4f}) during the last {query.hours_back:.0f} hours."
            )
        else:
            top_evt = matched_events[0]
            summary = (
                f"Detected {len(matched_events)} coordinate-verified event(s) within {query.radius_km:.0f} km "
                f"over the last {query.hours_back:.0f} hours (closest: '{top_evt.title}' at {distances_km[top_evt.id]:.1f} km, "
                f"severity={top_evt.severity}, stage={top_evt.signal_stage}). "
                f"Correlated with {len(nearby_assets)} nearby infrastructure/institutional asset(s)."
            )

        return GeofenceIntelligenceResponse(
            query=query,
            matching_event_count=len(matched_events),
            events=matched_events,
            event_distances_km=distances_km,
            density_clusters=clusters,
            nearby_infrastructure_and_institutions=nearby_assets,
            summary=summary,
        )

    @classmethod
    def compute_density_clusters(
        cls,
        events: List[OSINTEvent],
        nearby_assets: Optional[List[Dict[str, Any]]] = None,
    ) -> List[GeofenceCluster]:
        """Group geo-located events into regional/city density clusters."""
        by_area: Dict[str, List[OSINTEvent]] = {}
        for evt in events:
            if evt.location.latitude is None or evt.location.longitude is None:
                continue
            key = f"{(evt.location.country or 'Unknown')}:{(evt.location.city or evt.location.region or 'Area')}"
            by_area.setdefault(key, []).append(evt)

        clusters: List[GeofenceCluster] = []
        for area_key, area_events in by_area.items():
            country, city_or_reg = area_key.split(":", 1)
            avg_lat = round(sum(e.location.latitude or 0.0 for e in area_events) / len(area_events), 4)
            avg_lon = round(sum(e.location.longitude or 0.0 for e in area_events) / len(area_events), 4)
            max_sev: OSINTSeverity = max(
                (e.severity for e in area_events),
                key=lambda s: SEVERITY_RANK.get(s, 1),
            )
            highest_stage: CrisisSignalStage = max(
                (e.signal_stage for e in area_events),
                key=lambda st: STAGE_ORDER.index(st) if st in STAGE_ORDER else 0,
            )
            asset_names = [
                str(a.get("name"))
                for a in (nearby_assets or [])
                if str(a.get("city") or "").lower() == city_or_reg.lower()
            ]
            clusters.append(
                GeofenceCluster(
                    cluster_id=f"cluster_{compute_content_hash(area_key)[:10]}",
                    center_latitude=avg_lat,
                    center_longitude=avg_lon,
                    country=country,
                    region_or_city=city_or_reg,
                    event_count=len(area_events),
                    max_severity=max_sev,
                    highest_stage=highest_stage,
                    event_ids=[e.id for e in area_events],
                    nearby_institutions_or_assets=asset_names,
                )
            )
        clusters.sort(key=lambda c: c.event_count, reverse=True)
        return clusters
