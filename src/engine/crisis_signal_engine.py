"""
AI-CRISS (CIP) — Event Deduplication, Source Independence Corroboration,
Conflict Preservation, and 5-Stage Crisis Signal Engine (Sections 14, 15, 16, 20).
"""

from datetime import datetime, timezone
import re
from typing import Dict, List, Optional, Tuple

from src.contracts.evidence_investigation import ProvenanceRecord
from src.contracts.osint_intelligence import (
    CorroborationStatus,
    CrisisSignal,
    CrisisSignalCategory,
    CrisisSignalStage,
    DuplicateMatchStatus,
    OSINTEvent,
    OSINTSource,
    SourceConflictRecord,
)
from src.security.ssrf_guard import compute_content_hash


SEVERITY_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
STAGE_ORDER: List[CrisisSignalStage] = [
    "OBSERVATION",
    "SIGNAL",
    "DEVELOPING_EVENT",
    "SIGNIFICANT_EVENT",
    "CRISIS_CANDIDATE",
]


def _tokenize_title(text: str) -> set:
    words = re.findall(r"[a-z0-9]{3,}", (text or "").lower())
    stop = {"the", "and", "for", "near", "with", "from", "into", "over", "report", "advisory", "official"}
    return {w for w in words if w not in stop}


class OSINTDeduplicationAndCorroborationEngine:
    """
    Implements Section 14 (Event Deduplication), Section 15 (Source Corroboration & Independence),
    and Section 16 (Conflicting Information Preservation).
    """

    @staticmethod
    def compute_event_similarity(evt_a: OSINTEvent, evt_b: OSINTEvent) -> float:
        """
        Compute composite similarity [0.0, 1.0] using:
        - normalized title token Jaccard similarity (weight 0.50)
        - location match (country + city, weight 0.25)
        - event_type / entity overlap (weight 0.15)
        - temporal proximity within 48h (weight 0.10)
        """
        tokens_a = _tokenize_title(evt_a.title)
        tokens_b = _tokenize_title(evt_b.title)
        if not tokens_a or not tokens_b:
            title_sim = 0.0
        else:
            title_sim = len(tokens_a & tokens_b) / max(1, len(tokens_a | tokens_b))

        loc_sim = 0.0
        if evt_a.location.country and evt_b.location.country:
            if evt_a.location.country.lower() == evt_b.location.country.lower():
                loc_sim += 0.4
                if (
                    evt_a.location.city
                    and evt_b.location.city
                    and evt_a.location.city.lower() == evt_b.location.city.lower()
                ):
                    loc_sim += 0.6

        type_sim = 1.0 if evt_a.event_type == evt_b.event_type else 0.0
        ent_a = set(evt_a.entities)
        ent_b = set(evt_b.entities)
        ent_sim = (len(ent_a & ent_b) / max(1, len(ent_a | ent_b))) if (ent_a and ent_b) else type_sim
        domain_sim = 0.6 * type_sim + 0.4 * ent_sim

        time_sim = 0.5
        if evt_a.first_observed and evt_b.first_observed:
            diff_hours = abs((evt_a.first_observed - evt_b.first_observed).total_seconds()) / 3600.0
            if diff_hours <= 24.0:
                time_sim = 1.0
            elif diff_hours <= 72.0:
                time_sim = 0.5
            else:
                time_sim = 0.0

        score = (0.50 * title_sim) + (0.25 * loc_sim) + (0.15 * domain_sim) + (0.10 * time_sim)
        return round(min(1.0, max(0.0, score)), 3)

    @classmethod
    def classify_duplicate_relationship(cls, similarity: float) -> DuplicateMatchStatus:
        """
        Do NOT automatically merge events when confidence is insufficient.
        - >= 0.78 -> confirmed_duplicate
        - 0.55 .. 0.779 -> probable_duplicate
        - 0.36 .. 0.549 -> candidate_duplicate
        - < 0.36 -> unique
        """
        if similarity >= 0.78:
            return "confirmed_duplicate"
        if similarity >= 0.55:
            return "probable_duplicate"
        if similarity >= 0.36:
            return "candidate_duplicate"
        return "unique"

    @staticmethod
    def detect_claim_conflict(evt_a: OSINTEvent, evt_b: OSINTEvent) -> Optional[SourceConflictRecord]:
        """
        Detect whether two sources reporting on the same underlying event disagree on
        severity, operational status, or factual claims (Section 16).
        """
        src_a = evt_a.source_ids[0] if evt_a.source_ids else "source_a"
        src_b = evt_b.source_ids[0] if evt_b.source_ids else "source_b"
        ev_refs = [e.id for e in (evt_a.evidence + evt_b.evidence)]

        # 1. Status conflict (e.g., one source says RESOLVED/restored while another says ACTIVE/ongoing outage)
        text_a = evt_a.description.lower()
        text_b = evt_b.description.lower()
        negation_conflict = (
            ("no outage" in text_b or "fully restored" in text_b or "false alarm" in text_b or evt_b.status == "RESOLVED")
            and ("outage" in text_a or "disruption" in text_a or "instability" in text_a)
            and evt_a.status == "ACTIVE"
        ) or (
            ("no outage" in text_a or "fully restored" in text_a or "false alarm" in text_a or evt_a.status == "RESOLVED")
            and ("outage" in text_b or "disruption" in text_b or "instability" in text_b)
            and evt_b.status == "ACTIVE"
        )

        # 2. Major severity divergence (e.g. LOW vs CRITICAL/HIGH on the same event)
        sev_diff = abs(SEVERITY_RANK.get(evt_a.severity, 2) - SEVERITY_RANK.get(evt_b.severity, 2))

        if negation_conflict or sev_diff >= 2:
            topic = "operational_status_and_impact" if negation_conflict else "severity_assessment"
            disagreement = (
                f"Source '{src_a}' reports [{evt_a.severity}/{evt_a.status}: '{evt_a.description[:140]}'] "
                f"whereas Source '{src_b}' reports [{evt_b.severity}/{evt_b.status}: '{evt_b.description[:140]}']."
            )
            cid = f"conf_{compute_content_hash(f'{evt_a.id}:{evt_b.id}:{topic}')[:12]}"
            return SourceConflictRecord(
                conflict_id=cid,
                event_id=evt_a.id,
                topic_or_field=topic,
                source_a_id=src_a,
                source_a_claim=f"[{evt_a.severity}/{evt_a.status}] {evt_a.description}",
                source_b_id=src_b,
                source_b_claim=f"[{evt_b.severity}/{evt_b.status}] {evt_b.description}",
                disagreement=disagreement,
                source_a_timestamp=evt_a.reported_at,
                source_b_timestamp=evt_b.reported_at,
                detected_at=datetime.now(timezone.utc),
                evidence=ev_refs,
                confidence=0.60,
                resolution_status="UNRESOLVED",
            )
        return None

    @classmethod
    def process_events(
        cls,
        incoming_events: List[OSINTEvent],
        sources_by_id: Dict[str, OSINTSource],
    ) -> Tuple[List[OSINTEvent], List[SourceConflictRecord]]:
        """
        Deduplicate, evaluate source independence, update corroboration status,
        and preserve all conflicting source claims without silent overwrites.
        """
        canonical_events: List[OSINTEvent] = []
        all_conflicts: List[SourceConflictRecord] = []

        for evt in incoming_events:
            best_match: Optional[OSINTEvent] = None
            best_sim = 0.0

            for existing in canonical_events:
                sim = cls.compute_event_similarity(existing, evt)
                if sim > best_sim:
                    best_sim = sim
                    best_match = existing

            dup_status = cls.classify_duplicate_relationship(best_sim) if best_match else "unique"

            if best_match and dup_status == "confirmed_duplicate":
                # Check for conflicting claims BEFORE corroborating
                conflict = cls.detect_claim_conflict(best_match, evt)
                if conflict:
                    best_match.conflicts.append(conflict)
                    all_conflicts.append(conflict)

                # Merge provenance & sources into canonical event while tracking independence
                for sid in evt.source_ids:
                    if sid not in best_match.source_ids:
                        best_match.source_ids.append(sid)
                best_match.source_count = len(best_match.source_ids)

                existing_ev_ids = {e.id for e in best_match.evidence}
                for ev in evt.evidence:
                    if ev.id not in existing_ev_ids:
                        best_match.evidence.append(ev)

                for raw_s in evt.epistemic_chain.raw_source:
                    if raw_s not in best_match.epistemic_chain.raw_source:
                        best_match.epistemic_chain.raw_source.append(raw_s)
                for ext_f in evt.epistemic_chain.extracted_facts:
                    if ext_f not in best_match.epistemic_chain.extracted_facts:
                        best_match.epistemic_chain.extracted_facts.append(ext_f)

                # Determine true source independence:
                # Multiple websites repeating the same wire origin_hash or independence_group count as 1 independent source!
                for o_hash in evt.origin_hashes:
                    if o_hash not in best_match.origin_hashes:
                        best_match.origin_hashes.append(o_hash)

                ind_groups = {
                    (sources_by_id[sid].independence_group or sid)
                    for sid in best_match.source_ids
                    if sid in sources_by_id
                } or set(best_match.source_ids)

                independent_count = min(len(ind_groups), max(1, len(best_match.origin_hashes)))
                best_match.independent_source_count = independent_count

                has_official = any(
                    sources_by_id[sid].is_official_source
                    for sid in best_match.source_ids
                    if sid in sources_by_id
                )

                if best_match.conflicts:
                    best_match.corroboration_status = "Conflicting-source signal"
                    best_match.status = "CONTESTED"
                    # Reduce confidence when conflicting sources exist
                    best_match.confidence = round(max(0.45, best_match.confidence - 0.15), 3)
                    best_match.epistemic_chain.correlated_facts.append(
                        f"CONFLICT DETECTED across sources {best_match.source_ids}: {best_match.conflicts[-1].disagreement}"
                    )
                elif has_official:
                    best_match.corroboration_status = "Official-source confirmation"
                    best_match.status = "CONFIRMED"
                    best_match.confirmed_at = datetime.now(timezone.utc)
                    if independent_count >= 2:
                        best_match.confidence = round(min(0.98, best_match.confidence + 0.08), 3)
                    best_match.epistemic_chain.correlated_facts.append(
                        f"Confirmed by official source ({independent_count} independent source stream(s))."
                    )
                elif independent_count >= 2:
                    best_match.corroboration_status = "Multi-source corroborated signal"
                    best_match.status = "CONFIRMED"
                    best_match.confirmed_at = datetime.now(timezone.utc)
                    best_match.confidence = round(min(0.95, best_match.confidence + 0.06 * (independent_count - 1)), 3)
                    best_match.epistemic_chain.correlated_facts.append(
                        f"Corroborated by {independent_count} independent sources ({', '.join(best_match.source_ids)})."
                    )
                else:
                    # Syndicated repeat of the same original report -> NEVER boost confidence!
                    best_match.corroboration_status = "Single-source signal"
                    best_match.epistemic_chain.correlated_facts.append(
                        f"Repeated across {best_match.source_count} sources sharing the same origin/syndicate; confidence unchanged."
                    )

            elif best_match and dup_status in ("probable_duplicate", "candidate_duplicate"):
                # Do NOT auto-merge when confidence is insufficient; record link & conflict if any
                evt.duplicate_status = dup_status
                evt.matched_event_id = best_match.id
                evt.duplicate_similarity = best_sim
                conflict = cls.detect_claim_conflict(best_match, evt)
                if conflict:
                    evt.conflicts.append(conflict)
                    evt.corroboration_status = "Conflicting-source signal"
                    evt.status = "CONTESTED"
                    best_match.conflicts.append(conflict)
                    best_match.corroboration_status = "Conflicting-source signal"
                    all_conflicts.append(conflict)
                evt.epistemic_chain.correlated_facts.append(
                    f"Flagged as '{dup_status}' (similarity={best_sim:.2f}) relative to event '{best_match.id}'; preserved separately without automatic merge."
                )
                canonical_events.append(evt)
            else:
                evt.duplicate_status = "unique"
                has_official = any(
                    sources_by_id[sid].is_official_source
                    for sid in evt.source_ids
                    if sid in sources_by_id
                )
                if has_official:
                    evt.corroboration_status = "Official-source confirmation"
                    evt.status = "CONFIRMED"
                    evt.confirmed_at = datetime.now(timezone.utc)
                else:
                    evt.corroboration_status = "Single-source signal"
                canonical_events.append(evt)

        return canonical_events, all_conflicts


class CrisisSignalEngine:
    """
    Section 20 Crisis Signal Engine.
    Detects:
    - sudden_event_spike
    - geographic_clustering
    - repeated_reports
    - unusual_terminology
    - escalation_indicator
    - infrastructure_disruption
    - cyber_indicator
    - supply_chain_disruption
    - humanitarian_indicator
    - conflict_indicator
    - environmental_indicator

    Staged progression:
    OBSERVATION -> SIGNAL -> DEVELOPING_EVENT -> SIGNIFICANT_EVENT -> CRISIS_CANDIDATE
    Never labels every signal a crisis.
    """

    EVENT_TYPE_TO_SIGNAL_CATEGORY: Dict[str, CrisisSignalCategory] = {
        "infrastructure_disruption": "infrastructure_disruption",
        "cyber_threat": "cyber_indicator",
        "supply_chain": "supply_chain_disruption",
        "climate_environmental": "environmental_indicator",
        "conflict": "conflict_indicator",
        "military": "escalation_indicator",
        "humanitarian": "humanitarian_indicator",
        "geopolitical": "escalation_indicator",
        "general_osint": "repeated_reports",
    }

    @classmethod
    def determine_event_stage(cls, evt: OSINTEvent, cluster_size: int = 1) -> CrisisSignalStage:
        """
        Determine the evidence-backed stage on the 5-stage ladder:
        OBSERVATION -> SIGNAL -> DEVELOPING_EVENT -> SIGNIFICANT_EVENT -> CRISIS_CANDIDATE
        """
        sev_rank = SEVERITY_RANK.get(evt.severity, 2)
        ind_sources = evt.independent_source_count
        is_official = evt.corroboration_status == "Official-source confirmation"
        is_contested = evt.corroboration_status == "Conflicting-source signal"

        # Contested signals without official resolution are capped at DEVELOPING_EVENT until verified
        if is_contested:
            return "DEVELOPING_EVENT" if sev_rank >= 3 else "SIGNAL"

        if sev_rank == 1 and ind_sources == 1:
            return "OBSERVATION"

        if sev_rank == 2 and ind_sources == 1 and not is_official:
            return "SIGNAL"

        if sev_rank >= 3 and (ind_sources >= 2 or is_official):
            if (sev_rank == 4 and (ind_sources >= 2 or is_official)) or (sev_rank >= 3 and ind_sources >= 2 and cluster_size >= 2):
                return "CRISIS_CANDIDATE"
            return "SIGNIFICANT_EVENT"

        if sev_rank >= 3 or ind_sources >= 2 or cluster_size >= 2:
            return "DEVELOPING_EVENT"

        return "SIGNAL"

    @classmethod
    def evaluate_signals(cls, events: List[OSINTEvent]) -> List[CrisisSignal]:
        """
        Evaluate normalized, deduplicated OSINT events and produce staged CrisisSignal objects.
        Also detects geographic clustering when multiple events occur in the same region/city.
        """
        city_counts: Dict[str, List[OSINTEvent]] = {}
        for evt in events:
            loc_key = f"{(evt.location.country or 'unknown').lower()}:{(evt.location.city or evt.location.region or 'unknown').lower()}"
            city_counts.setdefault(loc_key, []).append(evt)

        signals: List[CrisisSignal] = []
        for evt in events:
            loc_key = f"{(evt.location.country or 'unknown').lower()}:{(evt.location.city or evt.location.region or 'unknown').lower()}"
            cluster_events = city_counts.get(loc_key, [evt])
            stage = cls.determine_event_stage(evt, cluster_size=len(cluster_events))
            evt.signal_stage = stage

            cat = cls.EVENT_TYPE_TO_SIGNAL_CATEGORY.get(evt.event_type, "repeated_reports")
            prov_list: List[ProvenanceRecord] = [ev.provenance for ev in evt.evidence if ev.provenance]
            conflict_notes = [c.disagreement for c in evt.conflicts]

            rationale = (
                f"Stage '{stage}' assigned from severity={evt.severity}, "
                f"corroboration='{evt.corroboration_status}' "
                f"({evt.independent_source_count} independent source(s), {evt.source_count} total), "
                f"regional cluster size={len(cluster_events)}."
            )

            sig = CrisisSignal(
                signal_id=f"sig_{evt.id}",
                title=evt.title,
                category=cat,
                stage=stage,
                severity=evt.severity,
                confidence=evt.confidence,
                rationale=rationale,
                evidence=prov_list,
                sources=list(evt.source_ids),
                location=evt.location,
                timestamp=evt.last_updated,
                related_entities=list(evt.entities),
                related_events=[e.id for e in cluster_events if e.id != evt.id],
                has_conflicting_sources=bool(evt.conflicts),
                conflict_notes=conflict_notes,
            )
            signals.append(sig)

        # Emit an explicit geographic_clustering signal if >= 2 distinct events cluster in the same city/region
        for loc_key, loc_events in city_counts.items():
            unique_events = [e for e in loc_events if e.duplicate_status == "unique"]
            if len(unique_events) >= 2:
                first_ev = unique_events[0]
                combined_prov = [ev.provenance for e in unique_events for ev in e.evidence if ev.provenance]
                combined_sources = sorted({s for e in unique_events for s in e.source_ids})
                max_sev = max((e.severity for e in unique_events), key=lambda s: SEVERITY_RANK.get(s, 1))
                cluster_stage: CrisisSignalStage = "SIGNIFICANT_EVENT" if SEVERITY_RANK.get(max_sev, 2) >= 3 else "DEVELOPING_EVENT"
                signals.append(
                    CrisisSignal(
                        signal_id=f"sig_cluster_{compute_content_hash(loc_key)[:10]}",
                        title=f"Geographic Event Cluster in {first_ev.location.city or first_ev.location.region}, {first_ev.location.country}",
                        category="geographic_clustering",
                        stage=cluster_stage,
                        severity=max_sev,
                        confidence=round(sum(e.confidence for e in unique_events) / len(unique_events), 3),
                        rationale=f"{len(unique_events)} distinct events detected in {first_ev.location.city or first_ev.location.region} across sources {combined_sources}.",
                        evidence=combined_prov[:6],
                        sources=combined_sources,
                        location=first_ev.location,
                        timestamp=datetime.now(timezone.utc),
                        related_entities= sorted({ent for e in unique_events for ent in e.entities}),
                        related_events=[e.id for e in unique_events],
                    )
                )

        return signals
