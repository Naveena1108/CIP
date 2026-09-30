"""
Async Repository Layer for AI-CRISS (CIP) OSINT & Global Intelligence Subsystem.

Provides idempotent upsert and filtered query operations for:
- OSINTSourceModel
- OSINTEvidenceModel
- OSINTEntityModel
- OSINTEventModel
- OSINTRelationshipModel
- OSINTSourceConflictModel
Respects multi-tenant scope (global public intelligence + organization/institution scoped intelligence).
"""

import json
from typing import List, Optional
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from src.contracts.osint_intelligence import (
    OSINTSource,
    OSINTEvidence,
    OSINTEntity,
    OSINTEvent,
    OSINTRelationship,
    SourceConflictRecord,
)
from src.db.models import (
    OSINTSourceModel,
    OSINTEvidenceModel,
    OSINTEntityModel,
    OSINTEventModel,
    OSINTRelationshipModel,
    OSINTSourceConflictModel,
    UserModel,
)


class OSINTRepository:
    """Persistence and retrieval manager for OSINT intelligence objects."""

    @staticmethod
    def _scope_clause(model_cls, user: Optional[UserModel] = None, organization_id: Optional[str] = None, institution_id: Optional[str] = None):
        """
        Global OSINT records (organization_id IS NULL) are visible to authenticated analysts,
        while tenant-scoped OSINT records respect the user's organization_id / institution_id.
        """
        clauses = [model_cls.organization_id.is_(None)]
        eff_org = organization_id or (user.organization_id if user else None)
        eff_inst = institution_id or (user.primary_institution_id if user else None)
        if eff_org:
            clauses.append(model_cls.organization_id == eff_org)
        if eff_inst:
            clauses.append(model_cls.institution_id == eff_inst)
        return or_(*clauses)

    @classmethod
    async def upsert_source(cls, session: AsyncSession, source: OSINTSource) -> OSINTSourceModel:
        existing = await session.get(OSINTSourceModel, source.id)
        license_json = json.dumps(source.license_metadata or {})
        if existing:
            existing.name = source.name
            existing.source_type = source.type
            existing.url = source.url
            existing.publisher = source.publisher
            existing.independence_group = source.independence_group or source.publisher
            existing.is_official_source = source.is_official_source
            existing.collection_method = source.collection_method
            existing.reliability = source.reliability
            existing.license_metadata_json = license_json
            await session.flush()
            return existing

        row = OSINTSourceModel(
            id=source.id,
            name=source.name,
            source_type=source.type,
            url=source.url,
            publisher=source.publisher,
            independence_group=source.independence_group or source.publisher,
            is_official_source=source.is_official_source,
            collection_method=source.collection_method,
            reliability=source.reliability,
            license_metadata_json=license_json,
            created_at=source.timestamp,
        )
        session.add(row)
        await session.flush()
        return row

    @classmethod
    async def list_sources(cls, session: AsyncSession) -> List[OSINTSource]:
        stmt = select(OSINTSourceModel).order_by(OSINTSourceModel.reliability.desc())
        res = await session.execute(stmt)
        rows = res.scalars().all()
        out: List[OSINTSource] = []
        for r in rows:
            out.append(
                OSINTSource(
                    id=r.id,
                    name=r.name,
                    type=r.source_type,  # type: ignore[arg-type]
                    url=r.url,
                    publisher=r.publisher,
                    independence_group=r.independence_group,
                    is_official_source=bool(r.is_official_source),
                    collection_method=r.collection_method,
                    reliability=r.reliability,
                    timestamp=r.created_at,
                    license_metadata=json.loads(r.license_metadata_json or "{}"),
                )
            )
        return out

    @classmethod
    async def upsert_evidence(cls, session: AsyncSession, ev: OSINTEvidence) -> OSINTEvidenceModel:
        existing = await session.get(OSINTEvidenceModel, ev.id)
        prov_json = ev.provenance.model_dump_json()
        full_json = ev.model_dump_json()
        if existing:
            existing.source_id = ev.source_id
            existing.source_url = ev.source_url
            existing.content_hash = ev.content_hash
            existing.extracted_content = ev.extracted_content
            existing.captured_at = ev.captured_at
            existing.published_at = ev.published_at
            existing.extraction_method = ev.extraction_method
            existing.confidence = ev.confidence
            existing.title = ev.title
            existing.author = ev.author
            existing.language = ev.language
            existing.provenance_json = prov_json
            existing.payload_json = full_json
            await session.flush()
            return existing

        row = OSINTEvidenceModel(
            id=ev.id,
            source_id=ev.source_id,
            source_url=ev.source_url,
            content_hash=ev.content_hash,
            extracted_content=ev.extracted_content,
            captured_at=ev.captured_at,
            published_at=ev.published_at,
            extraction_method=ev.extraction_method,
            confidence=ev.confidence,
            title=ev.title,
            author=ev.author,
            language=ev.language,
            provenance_json=prov_json,
            payload_json=full_json,
        )
        session.add(row)
        await session.flush()
        return row

    @classmethod
    async def list_evidence(cls, session: AsyncSession, source_id: Optional[str] = None, query: Optional[str] = None) -> List[OSINTEvidence]:
        stmt = select(OSINTEvidenceModel).order_by(OSINTEvidenceModel.captured_at.desc())
        if source_id:
            stmt = stmt.where(OSINTEvidenceModel.source_id == source_id)
        res = await session.execute(stmt)
        rows = res.scalars().all()
        items = [OSINTEvidence.model_validate_json(r.payload_json) for r in rows]
        if query:
            q_low = query.lower()
            items = [
                i for i in items
                if q_low in i.extracted_content.lower()
                or (i.title and q_low in i.title.lower())
                or q_low in i.source_id.lower()
            ]
        return items

    @classmethod
    async def upsert_entity(cls, session: AsyncSession, ent: OSINTEntity) -> OSINTEntityModel:
        existing = await session.get(OSINTEntityModel, ent.id)
        payload_str = ent.model_dump_json()
        country = ent.location.country if ent.location else None
        city = ent.location.city if ent.location else None
        lat = ent.location.latitude if ent.location else None
        lon = ent.location.longitude if ent.location else None

        if existing:
            existing.name = ent.name
            existing.entity_type = ent.entity_type
            existing.stix_id = ent.stix_id
            existing.confidence = ent.confidence
            existing.first_seen = ent.first_seen
            existing.last_seen = ent.last_seen
            existing.country = country
            existing.city = city
            existing.latitude = lat
            existing.longitude = lon
            existing.organization_id = ent.organization_id
            existing.institution_id = ent.institution_id
            existing.payload_json = payload_str
            await session.flush()
            return existing

        row = OSINTEntityModel(
            id=ent.id,
            name=ent.name,
            entity_type=ent.entity_type,
            stix_id=ent.stix_id,
            confidence=ent.confidence,
            first_seen=ent.first_seen,
            last_seen=ent.last_seen,
            country=country,
            city=city,
            latitude=lat,
            longitude=lon,
            organization_id=ent.organization_id,
            institution_id=ent.institution_id,
            payload_json=payload_str,
        )
        session.add(row)
        await session.flush()
        return row

    @classmethod
    async def list_entities(
        cls,
        session: AsyncSession,
        user: Optional[UserModel] = None,
        entity_type: Optional[str] = None,
        query: Optional[str] = None,
        organization_id: Optional[str] = None,
        institution_id: Optional[str] = None,
    ) -> List[OSINTEntity]:
        stmt = select(OSINTEntityModel).where(
            cls._scope_clause(OSINTEntityModel, user, organization_id, institution_id)
        )
        if entity_type:
            stmt = stmt.where(OSINTEntityModel.entity_type == entity_type)
        res = await session.execute(stmt)
        rows = res.scalars().all()
        items = [OSINTEntity.model_validate_json(r.payload_json) for r in rows]
        if query:
            q_low = query.lower()
            items = [
                e for e in items
                if q_low in e.name.lower()
                or q_low in e.entity_type.lower()
                or any(q_low in a.lower() for a in e.aliases)
                or (e.description and q_low in e.description.lower())
            ]
        return items

    @classmethod
    async def upsert_event(cls, session: AsyncSession, evt: OSINTEvent) -> OSINTEventModel:
        existing = await session.get(OSINTEventModel, evt.id)
        payload_str = evt.model_dump_json()
        if existing:
            existing.fingerprint = evt.fingerprint
            existing.title = evt.title
            existing.description = evt.description
            existing.event_type = evt.event_type
            existing.severity = evt.severity
            existing.signal_stage = evt.signal_stage
            existing.corroboration_status = evt.corroboration_status
            existing.duplicate_status = evt.duplicate_status
            existing.matched_event_id = evt.matched_event_id
            existing.confidence = evt.confidence
            existing.source_count = evt.source_count
            existing.independent_source_count = evt.independent_source_count
            existing.status = evt.status
            existing.latitude = evt.location.latitude
            existing.longitude = evt.location.longitude
            existing.country = evt.location.country
            existing.region = evt.location.region
            existing.city = evt.location.city
            existing.first_observed = evt.first_observed
            existing.reported_at = evt.reported_at
            existing.last_updated = evt.last_updated
            existing.organization_id = evt.organization_id
            existing.institution_id = evt.institution_id
            existing.payload_json = payload_str
            await session.flush()
            return existing

        row = OSINTEventModel(
            id=evt.id,
            fingerprint=evt.fingerprint,
            title=evt.title,
            description=evt.description,
            event_type=evt.event_type,
            severity=evt.severity,
            signal_stage=evt.signal_stage,
            corroboration_status=evt.corroboration_status,
            duplicate_status=evt.duplicate_status,
            matched_event_id=evt.matched_event_id,
            confidence=evt.confidence,
            source_count=evt.source_count,
            independent_source_count=evt.independent_source_count,
            status=evt.status,
            latitude=evt.location.latitude,
            longitude=evt.location.longitude,
            country=evt.location.country,
            region=evt.location.region,
            city=evt.location.city,
            first_observed=evt.first_observed,
            reported_at=evt.reported_at,
            last_updated=evt.last_updated,
            organization_id=evt.organization_id,
            institution_id=evt.institution_id,
            payload_json=payload_str,
        )
        session.add(row)
        await session.flush()
        return row

    @classmethod
    async def list_events(
        cls,
        session: AsyncSession,
        user: Optional[UserModel] = None,
        event_type: Optional[str] = None,
        severity: Optional[str] = None,
        query: Optional[str] = None,
        organization_id: Optional[str] = None,
        institution_id: Optional[str] = None,
    ) -> List[OSINTEvent]:
        stmt = (
            select(OSINTEventModel)
            .where(cls._scope_clause(OSINTEventModel, user, organization_id, institution_id))
            .order_by(OSINTEventModel.first_observed.desc())
        )
        if event_type:
            stmt = stmt.where(OSINTEventModel.event_type == event_type)
        if severity:
            stmt = stmt.where(OSINTEventModel.severity == severity)
        res = await session.execute(stmt)
        rows = res.scalars().all()
        items = [OSINTEvent.model_validate_json(r.payload_json) for r in rows]
        if query:
            q_low = query.lower()
            items = [
                e for e in items
                if q_low in e.title.lower()
                or q_low in e.description.lower()
                or (e.location.country and q_low in e.location.country.lower())
                or (e.location.city and q_low in e.location.city.lower())
            ]
        return items

    @classmethod
    async def upsert_relationship(cls, session: AsyncSession, rel: OSINTRelationship) -> OSINTRelationshipModel:
        existing = await session.get(OSINTRelationshipModel, rel.id)
        payload_str = rel.model_dump_json()
        if existing:
            existing.source_entity = rel.source_entity
            existing.relationship_type = rel.relationship_type
            existing.target_entity = rel.target_entity
            existing.confidence = rel.confidence
            existing.evidence_ids_json = json.dumps(rel.evidence)
            existing.timestamp = rel.timestamp
            existing.payload_json = payload_str
            await session.flush()
            return existing

        row = OSINTRelationshipModel(
            id=rel.id,
            source_entity=rel.source_entity,
            relationship_type=rel.relationship_type,
            target_entity=rel.target_entity,
            confidence=rel.confidence,
            evidence_ids_json=json.dumps(rel.evidence),
            timestamp=rel.timestamp,
            payload_json=payload_str,
        )
        session.add(row)
        await session.flush()
        return row

    @classmethod
    async def list_relationships(cls, session: AsyncSession, entity_id: Optional[str] = None) -> List[OSINTRelationship]:
        stmt = select(OSINTRelationshipModel)
        if entity_id:
            stmt = stmt.where(
                or_(
                    OSINTRelationshipModel.source_entity == entity_id,
                    OSINTRelationshipModel.target_entity == entity_id,
                )
            )
        res = await session.execute(stmt)
        rows = res.scalars().all()
        return [OSINTRelationship.model_validate_json(r.payload_json) for r in rows]

    @classmethod
    async def upsert_conflict(cls, session: AsyncSession, conflict: SourceConflictRecord) -> OSINTSourceConflictModel:
        existing = await session.get(OSINTSourceConflictModel, conflict.conflict_id)
        payload_str = conflict.model_dump_json()
        if existing:
            existing.event_id = conflict.event_id
            existing.topic_or_field = conflict.topic_or_field
            existing.source_a_id = conflict.source_a_id
            existing.source_a_claim = conflict.source_a_claim
            existing.source_b_id = conflict.source_b_id
            existing.source_b_claim = conflict.source_b_claim
            existing.disagreement = conflict.disagreement
            existing.confidence = conflict.confidence
            existing.resolution_status = conflict.resolution_status
            existing.detected_at = conflict.detected_at
            existing.payload_json = payload_str
            await session.flush()
            return existing

        row = OSINTSourceConflictModel(
            id=conflict.conflict_id,
            event_id=conflict.event_id,
            topic_or_field=conflict.topic_or_field,
            source_a_id=conflict.source_a_id,
            source_a_claim=conflict.source_a_claim,
            source_b_id=conflict.source_b_id,
            source_b_claim=conflict.source_b_claim,
            disagreement=conflict.disagreement,
            confidence=conflict.confidence,
            resolution_status=conflict.resolution_status,
            detected_at=conflict.detected_at,
            payload_json=payload_str,
        )
        session.add(row)
        await session.flush()
        return row

    @classmethod
    async def list_conflicts(cls, session: AsyncSession, event_id: Optional[str] = None) -> List[SourceConflictRecord]:
        stmt = select(OSINTSourceConflictModel).order_by(OSINTSourceConflictModel.detected_at.desc())
        if event_id:
            stmt = stmt.where(OSINTSourceConflictModel.event_id == event_id)
        res = await session.execute(stmt)
        rows = res.scalars().all()
        return [SourceConflictRecord.model_validate_json(r.payload_json) for r in rows]
