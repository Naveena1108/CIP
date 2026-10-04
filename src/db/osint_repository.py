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


def _get_insert_dialect(session: AsyncSession):
    try:
        bind = session.get_bind()
        if bind and getattr(bind.dialect, "name", "") == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
            return insert
    except Exception:
        pass
    from sqlalchemy.dialects.postgresql import insert
    return insert


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
        license_json = json.dumps(source.license_metadata or {})
        insert_fn = _get_insert_dialect(session)
        stmt = insert_fn(OSINTSourceModel).values(
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
        stmt = stmt.on_conflict_do_update(
            index_elements=[OSINTSourceModel.id],
            set_={
                "name": stmt.excluded.name,
                "source_type": stmt.excluded.source_type,
                "url": stmt.excluded.url,
                "publisher": stmt.excluded.publisher,
                "independence_group": stmt.excluded.independence_group,
                "is_official_source": stmt.excluded.is_official_source,
                "collection_method": stmt.excluded.collection_method,
                "reliability": stmt.excluded.reliability,
                "license_metadata_json": stmt.excluded.license_metadata_json,
            },
        )
        await session.execute(stmt)
        await session.flush()
        res = await session.get(OSINTSourceModel, source.id)
        return res  # type: ignore

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
        prov_json = ev.provenance.model_dump_json()
        full_json = ev.model_dump_json()
        insert_fn = _get_insert_dialect(session)
        stmt = insert_fn(OSINTEvidenceModel).values(
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
        stmt = stmt.on_conflict_do_update(
            index_elements=[OSINTEvidenceModel.id],
            set_={
                "source_id": stmt.excluded.source_id,
                "source_url": stmt.excluded.source_url,
                "content_hash": stmt.excluded.content_hash,
                "extracted_content": stmt.excluded.extracted_content,
                "captured_at": stmt.excluded.captured_at,
                "published_at": stmt.excluded.published_at,
                "extraction_method": stmt.excluded.extraction_method,
                "confidence": stmt.excluded.confidence,
                "title": stmt.excluded.title,
                "author": stmt.excluded.author,
                "language": stmt.excluded.language,
                "provenance_json": stmt.excluded.provenance_json,
                "payload_json": stmt.excluded.payload_json,
            },
        )
        await session.execute(stmt)
        await session.flush()
        res = await session.get(OSINTEvidenceModel, ev.id)
        return res  # type: ignore

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
        payload_str = ent.model_dump_json()
        country = ent.location.country if ent.location else None
        city = ent.location.city if ent.location else None
        lat = ent.location.latitude if ent.location else None
        lon = ent.location.longitude if ent.location else None
        insert_fn = _get_insert_dialect(session)
        stmt = insert_fn(OSINTEntityModel).values(
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
        stmt = stmt.on_conflict_do_update(
            index_elements=[OSINTEntityModel.id],
            set_={
                "name": stmt.excluded.name,
                "entity_type": stmt.excluded.entity_type,
                "stix_id": stmt.excluded.stix_id,
                "confidence": stmt.excluded.confidence,
                "first_seen": stmt.excluded.first_seen,
                "last_seen": stmt.excluded.last_seen,
                "country": stmt.excluded.country,
                "city": stmt.excluded.city,
                "latitude": stmt.excluded.latitude,
                "longitude": stmt.excluded.longitude,
                "organization_id": stmt.excluded.organization_id,
                "institution_id": stmt.excluded.institution_id,
                "payload_json": stmt.excluded.payload_json,
            },
        )
        await session.execute(stmt)
        await session.flush()
        res = await session.get(OSINTEntityModel, ent.id)
        return res  # type: ignore

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
        payload_str = evt.model_dump_json()
        insert_fn = _get_insert_dialect(session)
        stmt = insert_fn(OSINTEventModel).values(
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
        stmt = stmt.on_conflict_do_update(
            index_elements=[OSINTEventModel.id],
            set_={
                "fingerprint": stmt.excluded.fingerprint,
                "title": stmt.excluded.title,
                "description": stmt.excluded.description,
                "event_type": stmt.excluded.event_type,
                "severity": stmt.excluded.severity,
                "signal_stage": stmt.excluded.signal_stage,
                "corroboration_status": stmt.excluded.corroboration_status,
                "duplicate_status": stmt.excluded.duplicate_status,
                "matched_event_id": stmt.excluded.matched_event_id,
                "confidence": stmt.excluded.confidence,
                "source_count": stmt.excluded.source_count,
                "independent_source_count": stmt.excluded.independent_source_count,
                "status": stmt.excluded.status,
                "latitude": stmt.excluded.latitude,
                "longitude": stmt.excluded.longitude,
                "country": stmt.excluded.country,
                "region": stmt.excluded.region,
                "city": stmt.excluded.city,
                "first_observed": stmt.excluded.first_observed,
                "reported_at": stmt.excluded.reported_at,
                "last_updated": stmt.excluded.last_updated,
                "organization_id": stmt.excluded.organization_id,
                "institution_id": stmt.excluded.institution_id,
                "payload_json": stmt.excluded.payload_json,
            },
        )
        await session.execute(stmt)
        await session.flush()
        res = await session.get(OSINTEventModel, evt.id)
        return res  # type: ignore

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
        payload_str = rel.model_dump_json()
        insert_fn = _get_insert_dialect(session)
        stmt = insert_fn(OSINTRelationshipModel).values(
            id=rel.id,
            source_entity=rel.source_entity,
            relationship_type=rel.relationship_type,
            target_entity=rel.target_entity,
            confidence=rel.confidence,
            evidence_ids_json=json.dumps(rel.evidence),
            timestamp=rel.timestamp,
            payload_json=payload_str,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[OSINTRelationshipModel.id],
            set_={
                "source_entity": stmt.excluded.source_entity,
                "relationship_type": stmt.excluded.relationship_type,
                "target_entity": stmt.excluded.target_entity,
                "confidence": stmt.excluded.confidence,
                "evidence_ids_json": stmt.excluded.evidence_ids_json,
                "timestamp": stmt.excluded.timestamp,
                "payload_json": stmt.excluded.payload_json,
            },
        )
        await session.execute(stmt)
        await session.flush()
        res = await session.get(OSINTRelationshipModel, rel.id)
        return res  # type: ignore

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
        payload_str = conflict.model_dump_json()
        insert_fn = _get_insert_dialect(session)
        stmt = insert_fn(OSINTSourceConflictModel).values(
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
        stmt = stmt.on_conflict_do_update(
            index_elements=[OSINTSourceConflictModel.id],
            set_={
                "event_id": stmt.excluded.event_id,
                "topic_or_field": stmt.excluded.topic_or_field,
                "source_a_id": stmt.excluded.source_a_id,
                "source_a_claim": stmt.excluded.source_a_claim,
                "source_b_id": stmt.excluded.source_b_id,
                "source_b_claim": stmt.excluded.source_b_claim,
                "disagreement": stmt.excluded.disagreement,
                "confidence": stmt.excluded.confidence,
                "resolution_status": stmt.excluded.resolution_status,
                "detected_at": stmt.excluded.detected_at,
                "payload_json": stmt.excluded.payload_json,
            },
        )
        await session.execute(stmt)
        await session.flush()
        res = await session.get(OSINTSourceConflictModel, conflict.conflict_id)
        return res  # type: ignore

    @classmethod
    async def list_conflicts(cls, session: AsyncSession, event_id: Optional[str] = None) -> List[SourceConflictRecord]:
        stmt = select(OSINTSourceConflictModel).order_by(OSINTSourceConflictModel.detected_at.desc())
        if event_id:
            stmt = stmt.where(OSINTSourceConflictModel.event_id == event_id)
        res = await session.execute(stmt)
        rows = res.scalars().all()
        return [SourceConflictRecord.model_validate_json(r.payload_json) for r in rows]
