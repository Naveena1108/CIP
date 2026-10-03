"""
Database Models for AI CRISS Relational Persistence Layer.
Complies with DDR Module 1 specification using SQLAlchemy 2.0 DeclarativeBase.
"""

from datetime import datetime, timezone
from typing import Optional, List
from sqlalchemy import String, Integer, Float, DateTime, Text, ForeignKey, Boolean, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class OrganizationModel(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_category: Mapped[str] = mapped_column(String(64), default="educational_institution")
    entity_category_other: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ownership_governance: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    ownership_governance_other: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    education_entity_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    education_entity_type_other: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    university_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    university_type_other: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    academic_domains_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    academic_domain_other: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    state: Mapped[str] = mapped_column(String(64), default="Karnataka")
    city: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    website: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    owner_user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )


class InstitutionModel(Base):
    __tablename__ = "institutions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(64), default="Karnataka")
    city: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    accreditation_grade: Mapped[str] = mapped_column(String(16), default="A")
    entity_category: Mapped[Optional[str]] = mapped_column(String(64), default="educational_institution")
    entity_category_other: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    entity_type: Mapped[str] = mapped_column(String(64), default="institution")
    entity_type_other: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ownership_governance: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    ownership_governance_other: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    education_level: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    education_entity_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    education_entity_type_other: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    university_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    university_type_other: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    academic_domain: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    academic_domains_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    academic_domain_other: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    parent_organization_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    organization_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    owner_user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    established_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    website: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    contact_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    regulatory_body: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    affiliation_details: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )

    snapshots: Mapped[List["SignalSnapshotModel"]] = relationship(
        back_populates="institution", cascade="all, delete-orphan"
    )
    assessments: Mapped[List["CrisisAssessmentModel"]] = relationship(
        back_populates="institution", cascade="all, delete-orphan"
    )
    hierarchy_nodes: Mapped[List["HierarchyNodeModel"]] = relationship(
        back_populates="institution", cascade="all, delete-orphan"
    )


class HierarchyNodeModel(Base):
    """
    Extensible structural hierarchy node supporting:
    - University -> Campuses/Schools/Faculties -> Departments -> Programs -> Data/Signals
    - Institution -> Departments -> Programs -> Data/Signals
    - Organization/Group -> Institutions -> Departments -> Programs -> Data/Signals
    """
    __tablename__ = "hierarchy_nodes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    institution_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    parent_node_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    node_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # campus_school_faculty, department, program, other
    node_type_other: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    academic_domain: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    academic_domain_other: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    degree_or_level: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    sanctioned_intake: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    owner_user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )

    institution: Mapped["InstitutionModel"] = relationship(back_populates="hierarchy_nodes")


class SignalSnapshotModel(Base):
    __tablename__ = "signal_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    institution_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    academic_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    department: Mapped[str] = mapped_column(String(64), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # admissions, placements, cet_ranking
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    provenance_id: Mapped[str] = mapped_column(String(128), default="UNKNOWN")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )

    institution: Mapped["InstitutionModel"] = relationship(back_populates="snapshots")

    __table_args__ = (
        Index("idx_snap_inst_lookup", "institution_id", "academic_year", "department", "signal_type"),
    )


class CrisisAssessmentModel(Base):
    __tablename__ = "crisis_assessments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    institution_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dataset_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    signals_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    assessment_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )
    cri_score: Mapped[float] = mapped_column(Float, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False)
    primary_threat: Mapped[str] = mapped_column(String(128), nullable=False)
    assessment_json: Mapped[str] = mapped_column(Text, nullable=False)

    institution: Mapped["InstitutionModel"] = relationship(back_populates="assessments")

    __table_args__ = (
        Index("idx_assess_inst_time", "institution_id", "assessment_timestamp"),
        Index("idx_assess_inst_ds_ver", "institution_id", "dataset_version"),
    )


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="Viewer")  # SuperAdmin, Auditor, Analyst, Viewer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    auth_provider: Mapped[str] = mapped_column(String(32), default="local")
    google_sub: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, unique=True, index=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    job_title: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    department_or_unit: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    organization_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    primary_institution_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )


class OTPVerificationModel(Base):
    """
    Dedicated persistence table for real backend OTP verification (email login & signup).
    Enforces expiration, attempt limits, and single-use invalidation.
    """
    __tablename__ = "otp_verifications"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # 'login', 'signup', 'reset_password'
    otp_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_otp_email_purpose", "email", "purpose", "used_at"),
    )


class RevokedTokenModel(Base):
    """
    Persisted revoked JWT token registry for immediate server-side logout enforcement.
    Prevents reuse of revoked tokens even across serverless worker restarts.
    """
    __tablename__ = "revoked_tokens"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256 token hash
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_revoked_tokens_exp", "expires_at"),
    )


class DiscoveredSignalModel(Base):
    """
    Persists dynamically discovered signals across all institutional domains with
    full provenance, evidence-backed context, and visible contradiction tracking.
    Never silently overwrites conflicting observations from different sources.
    """
    __tablename__ = "discovered_signals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ingestion_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    organization_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    institution_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("institutions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    department: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    program: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    time_period: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    academic_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    domain: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    metric_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    metric_label: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    raw_value: Mapped[str] = mapped_column(String(255), nullable=False)
    unit: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    polarity: Mapped[str] = mapped_column(String(32), default="neutral")
    risk_contribution: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_contradictory: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    contradiction_group_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
    source_document: Mapped[str] = mapped_column(String(255), nullable=False)
    format_type: Mapped[str] = mapped_column(String(32), nullable=False)
    page_or_section: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    spreadsheet_location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    excerpt_or_reference: Mapped[str] = mapped_column(Text, nullable=False)
    extraction_confidence: Mapped[float] = mapped_column(Float, default=0.9)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("idx_disc_sig_fp", "institution_id", "fingerprint"),
        Index("idx_disc_sig_ident", "institution_id", "domain", "metric_name", "academic_year", "department"),
        Index("idx_disc_sig_inst_ingest", "institution_id", "ingestion_id"),
    )


class IngestionRecordModel(Base):
    """
    Audit log of every universal ingestion operation, including pipeline stages,
    detected format, truthful explanations, and data quality / contradiction reports.
    """
    __tablename__ = "ingestion_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    institution_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    owner_user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    dataset_version: Mapped[int] = mapped_column(Integer, default=1)
    detected_format: Mapped[str] = mapped_column(String(32), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    truthful_explanation: Mapped[str] = mapped_column(Text, nullable=False)
    total_signals: Mapped[int] = mapped_column(Integer, default=0)
    contradictions_detected: Mapped[int] = mapped_column(Integer, default=0)
    duplicates_detected: Mapped[int] = mapped_column(Integer, default=0)
    extraction_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("idx_ingest_inst_time", "institution_id", "created_at"),
    )


class InstitutionalMemoryEntryModel(Base):
    """
    Persists CIP Phase 4 longitudinal institutional memory across 7 distinct categories:
    observed_fact, analysis, inference, prediction, outcome, user_feedback, unknown.
    Also supports interventions, hypotheses, and prediction/outcome comparisons.
    """
    __tablename__ = "institutional_memory_entries"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    institution_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    organization_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    metric_or_topic: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    academic_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    evidence_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_by_user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )


class OSINTSourceModel(Base):
    """Canonical persistence table for OSINT and Threat Intelligence sources."""
    __tablename__ = "osint_sources"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    publisher: Mapped[str] = mapped_column(String(255), nullable=False)
    independence_group: Mapped[str] = mapped_column(String(128), default="", index=True)
    is_official_source: Mapped[bool] = mapped_column(Boolean, default=False)
    collection_method: Mapped[str] = mapped_column(String(128), nullable=False)
    reliability: Mapped[float] = mapped_column(Float, default=0.8)
    license_metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )


class OSINTEvidenceModel(Base):
    """Content-hashed OSINT evidence record with 7-field provenance."""
    __tablename__ = "osint_evidence"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    extracted_content: Mapped[str] = mapped_column(Text, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    extraction_method: Mapped[str] = mapped_column(String(128), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.85)
    title: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    author: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    language: Mapped[str] = mapped_column(String(16), default="en")
    provenance_json: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")


class OSINTEntityModel(Base):
    """OSINT & STIX 2.1 Entity persistence model."""
    __tablename__ = "osint_entities"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    stix_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.85)
    first_seen: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    city: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    organization_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    institution_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )


class OSINTEventModel(Base):
    """Global & Crisis OSINT Event persistence model with temporal, geospatial, and epistemic separation."""
    __tablename__ = "osint_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    signal_stage: Mapped[str] = mapped_column(String(32), default="OBSERVATION", index=True)
    corroboration_status: Mapped[str] = mapped_column(String(64), default="Single-source signal")
    duplicate_status: Mapped[str] = mapped_column(String(32), default="unique", index=True)
    matched_event_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.75)
    source_count: Mapped[int] = mapped_column(Integer, default=1)
    independent_source_count: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", index=True)
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True, index=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True, index=True)
    country: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    region: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    first_observed: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True
    )
    reported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )
    organization_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    institution_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class OSINTRelationshipModel(Base):
    """Provenance-backed relationship edge between OSINT entities/events."""
    __tablename__ = "osint_relationships"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_entity: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    relationship_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_entity: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.85)
    evidence_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class OSINTSourceConflictModel(Base):
    """Persists conflicting source claims without silently overwriting disagreements."""
    __tablename__ = "osint_source_conflicts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    topic_or_field: Mapped[str] = mapped_column(String(128), nullable=False)
    source_a_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_a_claim: Mapped[str] = mapped_column(Text, nullable=False)
    source_b_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_b_claim: Mapped[str] = mapped_column(Text, nullable=False)
    disagreement: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.6)
    resolution_status: Mapped[str] = mapped_column(String(64), default="UNRESOLVED")
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


