"""
Tests for Binary PDF Generation and PDF Reporting API Route.
Verifies reportlab rendering, evidence token embedding, and endpoint responses.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.db.models import Base
from src.db.session import get_db_session
from src.engine.evidence import InstitutionalDossier, EvidenceToken
from src.engine.llm_reasoner import ExecutiveNarrativeResponse
from src.engine.predictor import TrajectoryPoint
from src.reporting.pdf_generator import generate_crisis_pdf


@pytest.fixture
def sample_dossier():
    return InstitutionalDossier(
        institution_id="INST_PDF_001",
        risk_level="CRITICAL",
        composite_risk_index=0.875,
        primary_threat="Admissions and Placement Collapse",
        evidence_tokens=[
            EvidenceToken(
                signal_name="Admissions",
                metric_name="vacancy_rate",
                observed_value=0.68,
                baseline_value=0.22,
                deviation_zscore=3.75,
                severity="CRITICAL",
                academic_year=2024,
                narrative_fragment="Vacancy rate surged to 68.0% compared to baseline of 22.0%",
            ),
            EvidenceToken(
                signal_name="Placements",
                metric_name="placement_pct",
                observed_value=32.0,
                baseline_value=78.5,
                deviation_zscore=-4.12,
                severity="CRITICAL",
                academic_year=2024,
                narrative_fragment="Campus placement percentage dropped precipitously to 32.0%",
            ),
        ],
        total_anomalies=2,
        signal_coverage={"admissions": True, "placements": True, "cet_ranking": False},
        provenance_chain=["ACID_PERSISTENCE_STORE", "EXCEL_ADAPTER_HASH_e3b0c44"],
        recommended_mitigations=["Initiate urgent curriculum overhaul and corporate outreach"],
    )


@pytest.fixture
def sample_narrative():
    return ExecutiveNarrativeResponse(
        institution_id="INST_PDF_001",
        risk_level="CRITICAL",
        composite_risk_index=0.875,
        executive_summary="The institution is facing immediate structural distress characterized by cascading drop in enrollment and placement efficacy.",
        root_causes=[
            "Severe curriculum obsolescence failing employer technical requirements",
            "Sharp decline in top-tier student preference during state counseling",
        ],
        prioritized_actions=[
            "Freeze hiring and unviable elective branches immediately",
            "Sign MOUs with tier-1 technology partners for capstone internships",
        ],
        investigation_priorities=[
            "Audit departmental faculty retention over past 3 academic cycles",
        ],
        audit_provenance_summary="Grounding verified against cryptographic evidence tokens.",
    )


@pytest.fixture
def sample_trajectory():
    return [
        TrajectoryPoint(year_offset=1, projected_cri=0.910, confidence_band_low=0.830, confidence_band_high=0.990, scenario="STATUS_QUO"),
        TrajectoryPoint(year_offset=2, projected_cri=0.945, confidence_band_low=0.785, confidence_band_high=1.000, scenario="STATUS_QUO"),
        TrajectoryPoint(year_offset=3, projected_cri=0.970, confidence_band_low=0.730, confidence_band_high=1.000, scenario="STATUS_QUO"),
    ]


def test_generate_crisis_pdf_success(sample_dossier, sample_narrative, sample_trajectory):
    """Verify generate_crisis_pdf produces valid binary PDF content."""
    pdf_bytes = generate_crisis_pdf(sample_dossier, sample_narrative, sample_trajectory)
    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 2000


def test_generate_crisis_pdf_empty_anomalies(sample_narrative):
    """Verify PDF generator handles healthy dossiers with zero anomalies gracefully."""
    dossier = InstitutionalDossier(
        institution_id="INST_HEALTHY",
        risk_level="NOMINAL",
        composite_risk_index=0.120,
        primary_threat="None",
        evidence_tokens=[],
        total_anomalies=0,
        signal_coverage={"admissions": True, "placements": True, "cet_ranking": True},
        provenance_chain=["ACID_PERSISTENCE_STORE"],
    )
    pdf_bytes = generate_crisis_pdf(dossier, sample_narrative, trajectory=None)
    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000


def test_generate_crisis_pdf_all_risk_levels(sample_narrative):
    """Verify formatting across critical, elevated, and nominal risk tiers."""
    for rl, cri in [("CRITICAL", 0.85), ("ELEVATED", 0.55), ("NOMINAL", 0.15)]:
        dossier = InstitutionalDossier(
            institution_id=f"INST_{rl}",
            risk_level=rl,
            composite_risk_index=cri,
            primary_threat="Multi-Factor",
            evidence_tokens=[],
            total_anomalies=0,
        )
        pdf_bytes = generate_crisis_pdf(dossier, sample_narrative)
        assert pdf_bytes.startswith(b"%PDF")


@pytest_asyncio.fixture
async def api_client():
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db_session] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

    app.dependency_overrides.clear()
    await test_engine.dispose()


@pytest.mark.anyio
async def test_pdf_report_api_endpoint(api_client: AsyncClient):
    """Verify GET /api/v1/institutions/{id}/report/pdf returns valid binary PDF."""
    # Register & Login
    await api_client.post(
        "/api/v1/auth/register",
        json={
            "user_id": "pdf_auditor",
            "email": "auditor@aicriss.edu",
            "password": "SecurePassword123!",
            "role": "SuperAdmin",
        },
    )
    login_resp = await api_client.post(
        "/api/v1/auth/login",
        data={"username": "auditor@aicriss.edu", "password": "SecurePassword123!"},
    )
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Ingest synthetic data
    await api_client.post(
        "/api/v1/ingest/synthetic",
        headers=headers,
        json={
            "institution_id": "INST_PDF_API",
            "scenario": "CASCADING_CRISIS",
            "start_year": 2020,
            "num_years": 5,
        },
    )

    # Request PDF report
    resp = await api_client.get("/api/v1/institutions/INST_PDF_API/report/pdf", headers=headers)
    assert resp.status_code == 200
    assert "application/pdf" in resp.headers["content-type"]
    assert "criss_report_INST_PDF_API.pdf" in resp.headers.get("content-disposition", "")
    assert resp.content.startswith(b"%PDF")
    assert len(resp.content) > 2000


def test_pdf_decompressed_content_verification(sample_dossier, sample_narrative, sample_trajectory):
    """Verify decompressed binary PDF content stream contains all required institutional and audit sections."""
    import base64
    import zlib

    pdf_bytes = generate_crisis_pdf(sample_dossier, sample_narrative, sample_trajectory)
    idx1 = pdf_bytes.find(b"stream") + 6
    idx2 = pdf_bytes.find(b"endstream")
    raw_stream = pdf_bytes[idx1:idx2].strip()
    if raw_stream.endswith(b"~>"):
        raw_stream = raw_stream[:-2]
    decompressed_text = zlib.decompress(base64.a85decode(raw_stream)).decode("latin-1")

    assert "INST_PDF_001" in decompressed_text
    assert "0.875" in decompressed_text
    assert "CRITICAL" in decompressed_text
    assert "Executive Intelligence Synthesis" in decompressed_text
    assert "Statistical Anomaly Audit" in decompressed_text
    assert "Multi-Year Autoregressive Trajectory" in decompressed_text
    assert "Mathematical Invariant Disclosure" in decompressed_text
    assert "Sign MOUs with tier-1 technology partners" in decompressed_text

