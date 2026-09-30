"""Tests for Database & Persistence Layer (src/db/)."""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from src.db.models import Base
from src.db.repository import (
    InstitutionRepository,
    SignalSnapshotRepository,
    AssessmentRepository,
    UserRepository
)
from src.contracts import (
    ProvenanceMetadata,
    AdmissionsSignal,
    CrisisAssessment,
    SignalAnomaly
)

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

@pytest_asyncio.fixture
async def test_session():
    test_engine = create_async_engine(TEST_DB_URL, echo=False)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session

    await test_engine.dispose()

@pytest.mark.anyio
async def test_institution_crud(test_session: AsyncSession):
    inst = await InstitutionRepository.upsert(
        test_session,
        institution_id="INST_TEST",
        name="Test Institute of Technology",
        state="Karnataka",
        grade="A++"
    )
    assert inst.id == "INST_TEST"
    assert inst.name == "Test Institute of Technology"

    retrieved = await InstitutionRepository.get_by_id(test_session, "INST_TEST")
    assert retrieved is not None
    assert retrieved.name == "Test Institute of Technology"
    assert retrieved.accreditation_grade == "A++"

@pytest.mark.anyio
async def test_signal_snapshot_save_and_retrieve(test_session: AsyncSession):
    await InstitutionRepository.upsert(test_session, "INST_TEST", "Test Institute")

    prov = ProvenanceMetadata(source_id="TEST_SOURCE", source_type="synthetic_generated")
    signals = [
        AdmissionsSignal(
            institution_id="INST_TEST",
            academic_year=2023,
            department="CSE",
            sanctioned_intake=120,
            enrolled_count=100,
            vacancy_count=20,
            vacancy_rate=0.1667,
            provenance=prov
        ),
        AdmissionsSignal(
            institution_id="INST_TEST",
            academic_year=2024,
            department="CSE",
            sanctioned_intake=120,
            enrolled_count=80,
            vacancy_count=40,
            vacancy_rate=0.3333,
            provenance=prov
        )
    ]
    saved_count = await SignalSnapshotRepository.save_signals(test_session, signals)
    assert saved_count == 2

    snapshots = await SignalSnapshotRepository.get_by_institution(test_session, "INST_TEST", signal_type="admissions")
    assert len(snapshots) == 2
    assert snapshots[0].academic_year == 2023
    assert snapshots[1].academic_year == 2024
    assert snapshots[0].department == "CSE"

@pytest.mark.anyio
async def test_assessment_save_and_get_latest(test_session: AsyncSession):
    await InstitutionRepository.upsert(test_session, "INST_TEST", "Test Institute")

    assessment = CrisisAssessment(
        institution_id="INST_TEST",
        composite_risk_index=0.74,
        risk_level="CRITICAL",
        primary_driving_signal="Placement Collapse",
        confidence_score=0.91,
        anomalies_detected=[
            SignalAnomaly(
                signal_name="Placements",
                academic_year=2024,
                metric_name="placement_percentage",
                observed_value=18.0,
                baseline_value=75.0,
                deviation_zscore=-3.8,
                severity="CRITICAL",
                description="Collapse."
            )
        ],
        recommended_mitigations=["Realign curriculum"]
    )
    saved = await AssessmentRepository.save_assessment(test_session, assessment)
    assert saved.id is not None
    assert saved.cri_score == 0.74
    assert saved.risk_level == "CRITICAL"

    latest = await AssessmentRepository.get_latest(test_session, "INST_TEST")
    assert latest is not None
    assert latest.cri_score == 0.74
    assert "CRITICAL" in latest.risk_level

@pytest.mark.anyio
async def test_user_crud(test_session: AsyncSession):
    user = await UserRepository.create_user(
        test_session,
        user_id="usr_01",
        email="auditor@aicriss.org",
        hashed_password="hashed_secret_token",
        role="Auditor"
    )
    assert user.email == "auditor@aicriss.org"
    assert user.role == "Auditor"

    found = await UserRepository.get_by_email(test_session, "auditor@aicriss.org")
    assert found is not None
    assert found.id == "usr_01"


@pytest.mark.anyio
async def test_signal_snapshot_idempotent_upsert(test_session: AsyncSession):
    """Verify re-ingesting signals for the same institution/year/department/type updates in-place without row duplication."""
    await InstitutionRepository.upsert(test_session, "INST_IDEMPOTENT", "Idempotent Test Institute")
    prov = ProvenanceMetadata(source_id="TEST_SOURCE_V1", source_type="synthetic_generated")

    initial_signal = [
        AdmissionsSignal(
            institution_id="INST_IDEMPOTENT",
            academic_year=2024,
            department="CSE",
            sanctioned_intake=120,
            enrolled_count=100,
            vacancy_count=20,
            vacancy_rate=0.1667,
            provenance=prov
        )
    ]
    await SignalSnapshotRepository.save_signals(test_session, initial_signal)

    updated_prov = ProvenanceMetadata(source_id="TEST_SOURCE_V2", source_type="synthetic_generated")
    updated_signal = [
        AdmissionsSignal(
            institution_id="INST_IDEMPOTENT",
            academic_year=2024,
            department="CSE",
            sanctioned_intake=120,
            enrolled_count=60,
            vacancy_count=60,
            vacancy_rate=0.5000,
            provenance=updated_prov
        )
    ]
    await SignalSnapshotRepository.save_signals(test_session, updated_signal)

    snapshots = await SignalSnapshotRepository.get_by_institution(
        test_session, "INST_IDEMPOTENT", signal_type="admissions"
    )
    assert len(snapshots) == 1
    assert snapshots[0].provenance_id == "TEST_SOURCE_V2"
    assert '"enrolled_count":60' in snapshots[0].payload_json

