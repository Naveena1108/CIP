"""
AI-CRISS (CIP) — Provenance-Backed Temporal & Event Knowledge Graph Engine (Section 21).

Builds and queries graph topologies linking:
- Threat Actor
- Organization
- Infrastructure
- Indicator
- Entity
- Location
- Event
- Source
- Evidence
- Timeline

Example traversal supported:
  Threat Actor --[targets]--> Organization --[located-in]--> Location
  Location --[affected-by]--> Event --[reported-by]--> Source --[supported-by]--> Evidence
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.contracts.osint_intelligence import (
    KnowledgeGraphEdge,
    KnowledgeGraphNode,
    KnowledgeGraphSnapshot,
    OSINTEntity,
    OSINTEvent,
    OSINTEvidence,
    OSINTRelationship,
    OSINTSource,
)
from src.security.ssrf_guard import compute_content_hash


ENTITY_TYPE_TO_GRAPH_NODE_TYPE = {
    "Threat Actor": "Threat Actor",
    "Intrusion Set": "Threat Actor",
    "Organization": "Organization",
    "Infrastructure": "Infrastructure",
    "Indicator": "Indicator",
    "Observable": "Indicator",
    "Location": "Location",
    "Country": "Location",
    "City": "Location",
}


class OSINTKnowledgeGraphEngine:
    """Constructs and traverses the provenance-preserving OSINT Knowledge Graph."""

    @classmethod
    def build_graph(
        cls,
        sources: List[OSINTSource],
        evidence: List[OSINTEvidence],
        entities: List[OSINTEntity],
        events: List[OSINTEvent],
        relationships: List[OSINTRelationship],
    ) -> KnowledgeGraphSnapshot:
        nodes_by_id: Dict[str, KnowledgeGraphNode] = {}
        edges_by_id: Dict[str, KnowledgeGraphEdge] = {}

        def _add_node(node: KnowledgeGraphNode) -> None:
            if node.node_id not in nodes_by_id:
                nodes_by_id[node.node_id] = node
            else:
                existing = nodes_by_id[node.node_id]
                for pid in node.provenance_ids:
                    if pid not in existing.provenance_ids:
                        existing.provenance_ids.append(pid)

        def _add_edge(src_id: str, rel_type: str, tgt_id: str, conf: float, ev_ids: List[str], ts: Optional[datetime] = None) -> None:
            eid = f"kge_{compute_content_hash(f'{src_id}:{rel_type}:{tgt_id}')[:12]}"
            if eid not in edges_by_id:
                edges_by_id[eid] = KnowledgeGraphEdge(
                    edge_id=eid,
                    source_node_id=src_id,
                    relationship_type=rel_type,
                    target_node_id=tgt_id,
                    confidence=conf,
                    evidence_ids=list(ev_ids),
                    timestamp=ts or datetime.now(timezone.utc),
                )

        # 1. Source nodes
        for s in sources:
            _add_node(
                KnowledgeGraphNode(
                    node_id=s.id,
                    node_type="Source",
                    label=s.name,
                    attributes={"publisher": s.publisher, "url": s.url, "reliability": s.reliability, "is_official": s.is_official_source},
                    confidence=s.reliability,
                    provenance_ids=[s.id],
                )
            )

        # 2. Evidence nodes + Source -> supported-by -> Evidence
        for ev in evidence:
            _add_node(
                KnowledgeGraphNode(
                    node_id=ev.id,
                    node_type="Evidence",
                    label=ev.title or f"Evidence {ev.id}",
                    attributes={
                        "source_id": ev.source_id,
                        "source_url": ev.source_url,
                        "content_hash": ev.content_hash,
                        "extraction_method": ev.extraction_method,
                    },
                    confidence=ev.confidence,
                    provenance_ids=[ev.id],
                )
            )
            if ev.source_id in nodes_by_id:
                _add_edge(ev.source_id, "supported-by", ev.id, ev.confidence, [ev.id], ev.captured_at)

        # 3. Entity nodes (Threat Actor, Organization, Infrastructure, Indicator, Location, Entity)
        for ent in entities:
            g_type = ENTITY_TYPE_TO_GRAPH_NODE_TYPE.get(ent.entity_type, "Entity")
            _add_node(
                KnowledgeGraphNode(
                    node_id=ent.id,
                    node_type=g_type,  # type: ignore[arg-type]
                    label=ent.name,
                    attributes={
                        "entity_type": ent.entity_type,
                        "stix_id": ent.stix_id,
                        "aliases": ent.aliases,
                        **ent.attributes,
                    },
                    confidence=ent.confidence,
                    provenance_ids=list(ent.evidence_ids),
                )
            )

        # 4. Event nodes + Location nodes + Timeline nodes
        for evt in events:
            ev_ids = [e.id for e in evt.evidence]
            _add_node(
                KnowledgeGraphNode(
                    node_id=evt.id,
                    node_type="Event",
                    label=evt.title,
                    attributes={
                        "event_type": evt.event_type,
                        "severity": evt.severity,
                        "signal_stage": evt.signal_stage,
                        "corroboration_status": evt.corroboration_status,
                        "status": evt.status,
                    },
                    confidence=evt.confidence,
                    provenance_ids=ev_ids,
                )
            )

            # Timeline node for event temporal anchor
            day_label = evt.first_observed.strftime("%Y-%m-%d")
            tl_id = f"tl_{day_label}"
            _add_node(
                KnowledgeGraphNode(
                    node_id=tl_id,
                    node_type="Timeline",
                    label=f"Timeline {day_label}",
                    attributes={"date": day_label, "first_observed": evt.first_observed.isoformat()},
                    confidence=1.0,
                    provenance_ids=ev_ids,
                )
            )
            _add_edge(evt.id, "occurred-on", tl_id, 1.0, ev_ids, evt.first_observed)

            # Location node & Country/City hierarchy
            if evt.location.country:
                country_id = f"loc_country_{evt.location.country.lower().replace(' ', '_')}"
                _add_node(
                    KnowledgeGraphNode(
                        node_id=country_id,
                        node_type="Location",
                        label=evt.location.country,
                        attributes={
                            "country": evt.location.country,
                            "city": evt.location.city,
                            "latitude": evt.location.latitude,
                            "longitude": evt.location.longitude,
                        },
                        confidence=0.95,
                        provenance_ids=ev_ids,
                    )
                )
                _add_edge(country_id, "affected-by", evt.id, evt.confidence, ev_ids, evt.first_observed)

            # Event -> reported-by -> Source
            for sid in evt.source_ids:
                _add_edge(evt.id, "reported-by", sid, evt.confidence, ev_ids, evt.reported_at)

            # Linked entities -> involved-in / affected-by -> Event
            for ent_id in evt.entities:
                _add_edge(ent_id, "involved-in", evt.id, evt.confidence, ev_ids, evt.first_observed)

        # 5. Explicit Relationships (e.g. Threat Actor -> targets -> Organization -> located-in -> Country)
        for rel in relationships:
            _add_edge(
                rel.source_entity,
                rel.relationship_type,
                rel.target_entity,
                rel.confidence,
                rel.evidence,
                rel.timestamp,
            )

        nodes = list(nodes_by_id.values())
        edges = list(edges_by_id.values())
        return KnowledgeGraphSnapshot(
            total_nodes=len(nodes),
            total_edges=len(edges),
            nodes=nodes,
            edges=edges,
        )

    @classmethod
    def filter_entity_subgraph(cls, graph: KnowledgeGraphSnapshot, entity_or_node_id: str) -> KnowledgeGraphSnapshot:
        """Return immediate 1-hop and 2-hop neighborhood around a specific entity or event node."""
        q_low = entity_or_node_id.lower()
        seed_ids = {
            n.node_id
            for n in graph.nodes
            if n.node_id.lower() == q_low or q_low in n.label.lower()
        }
        if not seed_ids:
            return KnowledgeGraphSnapshot(total_nodes=0, total_edges=0, nodes=[], edges=[])

        hop1_edges = [
            e for e in graph.edges
            if e.source_node_id in seed_ids or e.target_node_id in seed_ids
        ]
        hop1_nodes = set(seed_ids)
        for e in hop1_edges:
            hop1_nodes.add(e.source_node_id)
            hop1_nodes.add(e.target_node_id)

        hop2_edges = [
            e for e in graph.edges
            if e.source_node_id in hop1_nodes or e.target_node_id in hop1_nodes
        ]
        hop2_nodes = set(hop1_nodes)
        for e in hop2_edges:
            hop2_nodes.add(e.source_node_id)
            hop2_nodes.add(e.target_node_id)

        sub_nodes = [n for n in graph.nodes if n.node_id in hop2_nodes]
        return KnowledgeGraphSnapshot(
            total_nodes=len(sub_nodes),
            total_edges=len(hop2_edges),
            nodes=sub_nodes,
            edges=hop2_edges,
        )

    @staticmethod
    def build_event_timeline(events: List[OSINTEvent], event_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Construct a chronological temporal intelligence timeline (Section 18) showing:
        first_observed, reported_at, confirmed_at, last_updated, resolved_at, escalation stage, and sources.
        """
        filtered = [e for e in events if not event_id or e.id == event_id]
        filtered.sort(key=lambda x: x.first_observed)
        timeline: List[Dict[str, Any]] = []
        for evt in filtered:
            milestones = [
                {"phase": "first_observed", "timestamp": evt.first_observed.isoformat()},
                {"phase": "reported", "timestamp": evt.reported_at.isoformat()},
            ]
            if evt.confirmed_at:
                milestones.append({"phase": "confirmed", "timestamp": evt.confirmed_at.isoformat()})
            milestones.append({"phase": "last_updated", "timestamp": evt.last_updated.isoformat()})
            if evt.resolved_at:
                milestones.append({"phase": "resolved", "timestamp": evt.resolved_at.isoformat()})

            timeline.append(
                {
                    "event_id": evt.id,
                    "title": evt.title,
                    "event_type": evt.event_type,
                    "severity": evt.severity,
                    "signal_stage": evt.signal_stage,
                    "corroboration_status": evt.corroboration_status,
                    "status": evt.status,
                    "first_observed": evt.first_observed.isoformat(),
                    "reported_at": evt.reported_at.isoformat(),
                    "confirmed_at": evt.confirmed_at.isoformat() if evt.confirmed_at else None,
                    "last_updated": evt.last_updated.isoformat(),
                    "resolved_at": evt.resolved_at.isoformat() if evt.resolved_at else None,
                    "location": evt.location.model_dump(),
                    "source_ids": evt.source_ids,
                    "evidence_ids": [ev.id for ev in evt.evidence],
                    "milestones": milestones,
                }
            )
        return timeline
