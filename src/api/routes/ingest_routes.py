"""
Universal Data Ingestion Route Handlers for CIP Phase 2.
Supports universal multi-format institutional data upload ("Add Data" / "Upload Data")
across PDF, XLSX, CSV, DOCX, PPTX, TXT/MD/JSON, Images/Scans, and Audio/Video,
while preserving legacy /excel and /synthetic endpoints for backward compatibility.
"""

import os
import hashlib
import tempfile
from typing import Any, Dict, List, Literal, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from src.runtime_env import is_deployed_environment

from src.db.session import get_db_session
from src.db.repository import (
    AccessPolicyRepository,
    AssessmentRepository,
    InstitutionRepository,
    OrganizationRepository,
    SignalSnapshotRepository,
    DiscoveredSignalRepository,
    IngestionRecordRepository,
)
from src.services.analysis_persistence import AnalysisPersistenceService
from src.adapters.excel_adapter import ExcelInstitutionalAdapter
from src.adapters.json_adapter import JSONDictionaryAdapter
from src.adapters.universal_ingestor import UniversalInstitutionalIngestor
from src.contracts import (
    AdmissionsSignal,
    PlacementsSignal,
    CETRankingSignal,
    ProvenanceMetadata,
    DiscoveredSignal,
    DataQualityIssue,
    UniversalIngestionResult,
)
from src.engine.synthetic_generator import SyntheticDataGenerator
from src.api.auth import get_current_user, require_role
from src.db.models import UserModel

router = APIRouter(prefix="/ingest", tags=["Universal Data Ingestion"])


class SyntheticIngestRequest(BaseModel):
    institution_id: str
    scenario: Literal[
        "HEALTHY",
        "ADMISSIONS_CRASH",
        "ADMISSIONS_DECLINE",
        "PLACEMENT_COLLAPSE",
        "PLACEMENT_DETERIORATION",
        "RANKING_DETERIORATION",
        "CROSS_SIGNAL_CRISIS",
        "CASCADING_CRISIS",
        "GRADUAL_DECLINE",
        "SUDDEN_CRISIS",
        "RECOVERY",
        "NOISY_MISSING_DATA"
    ] = "HEALTHY"
    start_year: int = 2020
    num_years: int = 5


class IngestionSummary(BaseModel):
    institution_id: str
    organization_id: Optional[str] = None
    status: str
    total_signals_ingested: int
    admissions_count: int
    placements_count: int
    cet_ranking_count: int


MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # 15 MB upload safety boundary


async def _handle_universal_upload(
    file: UploadFile,
    institution_id: Optional[str],
    organization_id: Optional[str],
    session: AsyncSession,
    current_user: UserModel,
    department: Optional[str] = None,
    program: Optional[str] = None,
):
    filename = file.filename or "uploaded_source"
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Uploaded file exceeds the 15 MB size limit.",
        )

    content_hash = hashlib.sha256(content).hexdigest()

    target_inst_id = (
        institution_id.strip()
        if institution_id and institution_id.strip()
        else (current_user.primary_institution_id if current_user.onboarding_completed else None)
    )
    target_org_id = (
        organization_id.strip()
        if organization_id and organization_id.strip()
        else (current_user.organization_id or None)
    )

    # Check for identical duplicate file upload via content_hash idempotency
    if target_inst_id:
        existing_rec = await IngestionRecordRepository.get_by_content_hash(session, target_inst_id, content_hash)
        if existing_rec:
            try:
                return UniversalIngestionResult.model_validate_json(existing_rec.result_json)
            except Exception:
                pass

    # Verify access if target_inst_id already exists
    if target_inst_id:
        existing_inst = await InstitutionRepository.get_by_id(session, target_inst_id)
        if existing_inst:
            if not InstitutionRepository.user_can_access(existing_inst, current_user):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Access denied: institution '{target_inst_id}' belongs to another user or organization.",
                )
            if not target_org_id:
                target_org_id = existing_inst.organization_id or existing_inst.parent_organization_id
        if (department or program) and not AccessPolicyRepository.user_can_access_department(
            current_user, target_inst_id, department or program
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: your department/program permission scope does not allow ingesting data for '{department or program}'.",
            )

    existing_discovered: List[DiscoveredSignal] = []
    if target_inst_id:
        existing_discovered = await DiscoveredSignalRepository.get_by_institution(session, target_inst_id)

    ingestor = UniversalInstitutionalIngestor(
        organization_id=target_org_id,
        institution_id=target_inst_id,
        existing_signals=existing_discovered,
    )
    result, canonical_map = ingestor.ingest_bytes(
        content=content,
        filename=filename,
        content_type=file.content_type,
    )

    # Resolve final institution ID
    final_inst_id = result.institution_id or target_inst_id or current_user.primary_institution_id
    if final_inst_id:
        existing_inst = await InstitutionRepository.get_by_id(session, final_inst_id)
        if existing_inst:
            if not InstitutionRepository.user_can_access(existing_inst, current_user):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Access denied: institution '{final_inst_id}' belongs to another user or organization.",
                )
            if not target_org_id:
                target_org_id = existing_inst.organization_id or existing_inst.parent_organization_id
        if not existing_inst and result.status in ("SUCCESS", "PARTIAL_EXTRACTION"):
            await InstitutionRepository.upsert(
                session,
                institution_id=final_inst_id,
                name=final_inst_id,
                organization_id=target_org_id,
                owner_user_id=current_user.id if current_user.onboarding_completed else None,
            )
        result.institution_id = final_inst_id
    if target_org_id:
        result.organization_id = target_org_id

    # Enforce department/program scope on discovered signals if user has DEPARTMENT_PROGRAM scope
    if final_inst_id and result.discovered_signals:
        allowed_depts = AccessPolicyRepository.get_allowed_departments_filter(current_user, final_inst_id)
        if allowed_depts is not None:
            allowed_set = set(allowed_depts)
            for s in result.discovered_signals:
                if department and not s.context.department:
                    s.context.department = department
                if program and not s.context.program:
                    s.context.program = program
                sig_dept = (s.context.department or s.context.program or "").strip().upper()
                if sig_dept and sig_dept not in allowed_set:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=(
                            f"Access denied: ingested signal targets department/program '{sig_dept}', "
                            f"which is outside your permitted scope {allowed_depts}."
                        ),
                    )
                if not sig_dept and allowed_depts:
                    s.context.department = allowed_depts[0]

    # If institution was discovered inside the file (when target_inst_id was initially None),
    # re-check contradictions against existing signals in DB for that discovered institution
    if final_inst_id and not target_inst_id and result.discovered_signals:
        existing_for_discovered = await DiscoveredSignalRepository.get_by_institution(session, final_inst_id)
        if existing_for_discovered:
            ingestor_recheck = UniversalInstitutionalIngestor(
                organization_id=target_org_id,
                institution_id=final_inst_id,
                existing_signals=existing_for_discovered,
            )
            dq_issues, contra_cnt, dup_cnt = ingestor_recheck._analyze_data_quality_and_contradictions(
                result.discovered_signals,
                [i for i in result.data_quality_issues if i.issue_type not in ("duplicates", "contradictory_sources")],
            )
            result.data_quality_issues = dq_issues
            result.contradictions_detected = contra_cnt
            result.duplicates_detected = dup_cnt
            existing_discovered = existing_for_discovered

    # Persist canonical signals if any were mapped
    all_canonical = (
        canonical_map.get("admissions", [])
        + canonical_map.get("placements", [])
        + canonical_map.get("cet_ranking", [])
    )
    if all_canonical and final_inst_id:
        for sig in all_canonical:
            sig.institution_id = final_inst_id
            if hasattr(sig, "provenance") and sig.provenance:
                sig.provenance.source_id = result.ingestion_id
        await SignalSnapshotRepository.save_signals(session, all_canonical)

    # Persist all dynamically discovered signals (never overwriting contradictory values)
    if result.discovered_signals:
        for s in result.discovered_signals:
            if final_inst_id and not s.context.institution_id:
                s.context.institution_id = final_inst_id
            if target_org_id and not s.context.organization_id:
                s.context.organization_id = target_org_id
            if department and not s.context.department:
                s.context.department = department
            if program and not s.context.program:
                s.context.program = program
        await DiscoveredSignalRepository.save_discovered_signals(
            session,
            ingestion_id=result.ingestion_id,
            signals=result.discovered_signals,
            existing_updated=existing_discovered,
        )

    await IngestionRecordRepository.save_record(
        session,
        result=result,
        owner_user_id=current_user.id,
        content_hash=content_hash,
    )

    if final_inst_id:
        await InstitutionRepository.set_active_dataset(session, final_inst_id, result.ingestion_id)
        AnalysisPersistenceService.invalidate_institution(final_inst_id)

    if result.status == "UNSUPPORTED_FORMAT":
        return JSONResponse(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            content=result.model_dump(mode="json"),
        )

    return result


@router.post("/upload", response_model=UniversalIngestionResult)
async def upload_universal_data(
    file: UploadFile = File(...),
    institution_id: Optional[str] = Form(default=None),
    organization_id: Optional[str] = Form(default=None),
    department: Optional[str] = Form(default=None),
    program: Optional[str] = Form(default=None),
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """
    Universal Institutional Data Ingestion ('Add Data' / 'Upload Data').
    Supports PDF, XLSX, CSV, DOCX, PPTX, TXT/MD/JSON, Images/Scans, and Audio/Video.
    """
    return await _handle_universal_upload(file, institution_id, organization_id, session, current_user, department, program)


@router.post("/data")
async def add_institutional_data(
    request: Request,
    file: Optional[UploadFile] = File(default=None),
    institution_id: Optional[str] = Form(default=None),
    organization_id: Optional[str] = Form(default=None),
    department: Optional[str] = Form(default=None),
    program: Optional[str] = Form(default=None),
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """
    Universal institutional data ingestion ('Add Data'):
    Supports both multipart file upload AND structured JSON signal payload
    ({"signal_type": "admissions"|"placements"|"cet_ranking", "payload": {...}}).
    """
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        body = await request.json()
        sig_type = (body.get("signal_type") or "").strip().lower()
        payload = body.get("payload") or {}
        target_inst_id = (
            payload.get("institution_id")
            or body.get("institution_id")
            or current_user.primary_institution_id
        )
        if not target_inst_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="institution_id is required in payload.",
            )
        target_dept = (payload.get("department") or body.get("department") or "GENERAL").strip()
        yr = int(payload.get("academic_year") or payload.get("graduation_year") or 2024)

        existing_inst = await InstitutionRepository.get_by_id(session, target_inst_id)
        target_org_id = body.get("organization_id") or current_user.organization_id
        if existing_inst:
            if not InstitutionRepository.user_can_access(existing_inst, current_user):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Access denied: institution '{target_inst_id}' belongs to another user or organization.",
                )
            if not target_org_id:
                target_org_id = existing_inst.organization_id or existing_inst.parent_organization_id

        if not AccessPolicyRepository.user_can_access_department(current_user, target_inst_id, target_dept):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: your department/program permission scope does not allow ingesting data for '{target_dept}'.",
            )

        prov = ProvenanceMetadata(
            source_id=f"structured_api_{sig_type}_{target_inst_id}_{target_dept}_{yr}",
            source_type="institutional_export",
        )
        if sig_type == "admissions":
            intake = int(payload.get("sanctioned_intake", 120))
            enrolled = int(payload.get("enrolled_count", payload.get("admitted_students", intake)))
            vac_cnt = int(payload.get("vacancy_count", max(0, intake - enrolled)))
            vac_rate = float(payload.get("vacancy_rate", round(vac_cnt / intake, 4) if intake > 0 else 0.0))
            dropouts = int(payload.get("dropouts_year_1", payload.get("dropout_count", 0)))
            sig = AdmissionsSignal(
                institution_id=target_inst_id,
                academic_year=yr,
                department=target_dept,
                sanctioned_intake=intake,
                enrolled_count=enrolled,
                vacancy_count=vac_cnt,
                vacancy_rate=vac_rate,
                dropouts_year_1=dropouts,
                provenance=prov,
            )
        elif sig_type == "placements":
            placed = int(payload.get("placed_students", 0))
            elig = int(payload.get("eligible_students", payload.get("graduating_cohort_size", max(100, placed))))
            if elig < placed:
                elig = placed
            unplaced = int(payload.get("unplaced_count", max(0, elig - placed)))
            placed_pct = float(
                payload.get("placement_percentage", min(100.0, round((placed / elig) * 100.0, 2)) if elig > 0 else 0.0)
            )
            med_sal = float(payload.get("median_salary_lpa", payload.get("average_package_lpa", 4.5)))
            max_sal = float(payload.get("max_salary_lpa", max(med_sal, med_sal * 1.5)))
            sig = PlacementsSignal(
                institution_id=target_inst_id,
                academic_year=yr,
                graduation_year=int(payload.get("graduation_year", yr)),
                department=target_dept,
                eligible_students=elig,
                placed_students=placed,
                unplaced_count=unplaced,
                placement_percentage=placed_pct,
                median_salary_lpa=med_sal,
                max_salary_lpa=max_sal,
                provenance=prov,
            )
        elif sig_type == "cet_ranking":
            open_r = int(payload.get("opening_rank", 1000))
            close_r = int(payload.get("closing_rank", max(open_r, 5000)))
            pct_cut = float(
                payload.get("percentile_cutoff", max(1.0, min(99.9, round(100.0 - (close_r / 1500.0), 2))))
            )
            sig = CETRankingSignal(
                institution_id=target_inst_id,
                academic_year=yr,
                department=target_dept,
                quota_category=str(payload.get("quota_category", "GM")),
                opening_rank=open_r,
                closing_rank=close_r,
                percentile_cutoff=pct_cut,
                provenance=prov,
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unsupported signal_type '{sig_type}'. Expected admissions, placements, or cet_ranking.",
            )

        if not existing_inst:
            await InstitutionRepository.upsert(
                session,
                institution_id=target_inst_id,
                name=target_inst_id,
                organization_id=target_org_id,
                owner_user_id=current_user.id if current_user.onboarding_completed else None,
            )

        await SignalSnapshotRepository.save_signals(session, [sig])
        AnalysisPersistenceService.invalidate_institution(target_inst_id)
        return JSONResponse(
            status_code=status.HTTP_201_CREATED,
            content={
                "status": "INGESTED",
                "institution_id": target_inst_id,
                "organization_id": target_org_id,
                "signal_type": sig_type,
                "department": target_dept,
                "academic_year": yr,
            },
        )

    if file is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Either an uploaded file or a JSON signal payload is required.",
        )
    return await _handle_universal_upload(file, institution_id, organization_id, session, current_user, department, program)


@router.get("/institutions/{institution_id}/signals", response_model=List[DiscoveredSignal])
async def list_discovered_signals(
    institution_id: str,
    response: Response,
    domain: Optional[str] = Query(default=None),
    department: Optional[str] = Query(default=None),
    contradictory_only: bool = Query(default=False),
    page: Optional[int] = Query(default=None, ge=1),
    page_size: Optional[int] = Query(default=None, ge=1, le=500),
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """List dynamically discovered signals and their provenance for an institution with optional pagination."""
    inst = await InstitutionRepository.get_by_id(session, institution_id)
    if inst and not InstitutionRepository.user_can_access(inst, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: institution '{institution_id}' belongs to another user or organization.",
        )
    if department and not AccessPolicyRepository.user_can_access_department(current_user, institution_id, department):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: your department/program permission scope does not allow viewing signals for '{department}'.",
        )
    signals = await DiscoveredSignalRepository.get_by_institution(
        session,
        institution_id=institution_id,
        domain=domain,
        contradictory_only=contradictory_only,
    )
    allowed_depts = AccessPolicyRepository.get_allowed_departments_filter(current_user, institution_id)
    if allowed_depts is not None:
        allowed_set = set(allowed_depts)
        signals = [
            s for s in signals
            if (s.context.department or "").strip().upper() in allowed_set
            or (s.context.program or "").strip().upper() in allowed_set
        ]
    if department:
        d_up = department.strip().upper()
        signals = [
            s for s in signals
            if (s.context.department or "").strip().upper() == d_up
            or (s.context.program or "").strip().upper() == d_up
        ]

    total_count = len(signals)
    response.headers["X-Total-Count"] = str(total_count)
    if page is not None or page_size is not None:
        p = page or 1
        ps = page_size or 50
        response.headers["X-Page"] = str(p)
        response.headers["X-Page-Size"] = str(ps)
        start = (p - 1) * ps
        return signals[start : start + ps]

    return signals


@router.get("/organizations/{organization_id}/signals")
async def list_organization_signals(
    organization_id: str,
    domain: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    List all ingested signals across constituent institutions of an organization/network.
    """
    org = await OrganizationRepository.get_by_id(session, organization_id)
    if org and not OrganizationRepository.user_can_access(org, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: organization '{organization_id}' belongs to another user or exceeds your permission scope.",
        )
    constituents = await InstitutionRepository.list_constituent_institutions(session, organization_id, current_user)
    if not org and not constituents:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Organization '{organization_id}' not found.",
        )

    by_inst: Dict[str, Any] = {}
    all_discovered: List[Dict[str, Any]] = []
    total_canonical = 0

    for inst in constituents:
        snaps = await SignalSnapshotRepository.get_by_institution(session, inst.id)
        dsigs = await DiscoveredSignalRepository.get_by_institution(session, inst.id, domain=domain)
        total_canonical += len(snaps)
        for ds in dsigs:
            if not ds.context.organization_id:
                ds.context.organization_id = organization_id
            all_discovered.append(ds.model_dump(mode="json"))
        by_inst[inst.id] = {
            "institution_id": inst.id,
            "institution_name": inst.name,
            "education_entity_type": inst.education_entity_type or inst.education_level,
            "canonical_snapshot_count": len(snaps),
            "discovered_signal_count": len(dsigs),
            "total_signal_count": len(snaps) + len(dsigs),
        }

    return {
        "organization_id": organization_id,
        "constituent_institution_count": len(constituents),
        "total_canonical_snapshots": total_canonical,
        "total_discovered_signals": len(all_discovered),
        "total_signals": total_canonical + len(all_discovered),
        "by_institution": by_inst,
        "discovered_signals": all_discovered,
    }


@router.get("/institutions/{institution_id}/records", response_model=List[UniversalIngestionResult])
async def list_ingestion_records(
    institution_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """List universal ingestion audit records for an institution."""
    inst = await InstitutionRepository.get_by_id(session, institution_id)
    if inst and not InstitutionRepository.user_can_access(inst, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: institution '{institution_id}' belongs to another user or organization.",
        )
    return await IngestionRecordRepository.list_by_institution(session, institution_id)


@router.get("/institutions/{institution_id}/datasets")
async def list_institution_datasets(
    institution_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
) -> List[Dict[str, Any]]:
    """List available uploaded datasets for an institution (Section 7: Dataset Management)."""
    inst = await InstitutionRepository.get_by_id(session, institution_id)
    if inst and not InstitutionRepository.user_can_access(inst, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: institution '{institution_id}' belongs to another user or organization.",
        )
    active_dataset_id = await InstitutionRepository.get_active_dataset(session, institution_id)
    records = await IngestionRecordRepository.list_by_institution(session, institution_id)
    datasets = []
    for idx, r in enumerate(records):
        fmt = getattr(r, "format_type", None) or getattr(r, "detected_format", None) or "UNKNOWN"
        sigs = getattr(r, "total_signals_discovered", None) or getattr(r, "total_signals_ingested", None) or len(getattr(r, "discovered_signals", [])) or 0
        doms = getattr(r, "domains_discovered", None)
        if not doms and hasattr(r, "detected_domains") and r.detected_domains:
            doms = list(r.detected_domains.keys())
        st = getattr(r, "status", None) or getattr(r, "processing_status", None) or "SUCCESS"
        is_active = (r.ingestion_id == active_dataset_id) if active_dataset_id else (idx == 0)
        datasets.append({
            "ingestion_id": r.ingestion_id,
            "filename": r.filename,
            "format_type": fmt,
            "total_signals_discovered": sigs,
            "domains": doms or [],
            "status": st,
            "is_active": is_active,
        })
    return datasets


@router.post("/institutions/{institution_id}/datasets/{ingestion_id}/select")
async def select_institution_dataset(
    institution_id: str,
    ingestion_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
) -> Dict[str, Any]:
    """Select and persist active dataset for analysis (Section 7: Active Dataset State)."""
    inst = await InstitutionRepository.get_by_id(session, institution_id)
    if inst and not InstitutionRepository.user_can_access(inst, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: institution '{institution_id}' belongs to another user or organization.",
        )
    rec = await IngestionRecordRepository.get_by_id(session, ingestion_id)
    if not rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dataset with ID '{ingestion_id}' not found.",
        )
    # Persist the active dataset selection in the database
    await InstitutionRepository.set_active_dataset(session, institution_id, ingestion_id)
    await session.commit()
    AnalysisPersistenceService.invalidate_institution(institution_id)
    return {
        "status": "SELECTED",
        "institution_id": institution_id,
        "active_dataset_id": ingestion_id,
        "filename": rec.filename,
    }


@router.delete("/institutions/{institution_id}/datasets/{ingestion_id}")
async def delete_institution_dataset(
    institution_id: str,
    ingestion_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Safely delete an uploaded dataset and cascade-delete its exclusive discovered signals
    and snapshots without leaving orphaned records (Section 8: Fix Dataset Deletion Completely).
    """
    inst = await InstitutionRepository.get_by_id(session, institution_id)
    if inst and not InstitutionRepository.user_can_access(inst, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: institution '{institution_id}' belongs to another user or organization.",
        )
    rec = await IngestionRecordRepository.get_by_id(session, ingestion_id)
    if not rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dataset with ID '{ingestion_id}' not found.",
        )
    filename = rec.filename
    # Cascading deletion of signals and snapshots
    deleted_signals = await DiscoveredSignalRepository.delete_by_ingestion_id(session, ingestion_id)
    deleted_snapshots = await SignalSnapshotRepository.delete_by_provenance_id(session, ingestion_id)
    # Deletion of ingestion record
    await IngestionRecordRepository.delete_by_id(session, ingestion_id)

    # If the active dataset was deleted, update active_dataset_id to next available dataset or None
    active_id = await InstitutionRepository.get_active_dataset(session, institution_id)
    if active_id == ingestion_id:
        remaining_records = await IngestionRecordRepository.list_by_institution(session, institution_id)
        next_active = remaining_records[0].ingestion_id if remaining_records else None
        await InstitutionRepository.set_active_dataset(session, institution_id, next_active)

    # Invalidate cached analysis for this institution
    AnalysisPersistenceService.invalidate_institution(institution_id)

    # If no remaining signals or snapshots exist, cascade delete stale assessment
    remaining_discovered = await DiscoveredSignalRepository.get_by_institution(session, institution_id)
    remaining_snapshots = await SignalSnapshotRepository.get_by_institution(session, institution_id)
    if not remaining_discovered and not remaining_snapshots:
        await AssessmentRepository.delete_by_institution(session, institution_id)

    await session.commit()

    return {
        "status": "DELETED",
        "institution_id": institution_id,
        "ingestion_id": ingestion_id,
        "filename": filename,
        "deleted_signals_count": deleted_signals,
        "deleted_snapshots_count": deleted_snapshots,
    }


@router.get("/institutions/{institution_id}/quality")
async def get_institution_data_quality(
    institution_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Retrieve comprehensive data quality report, visible contradictions, duplicates,
    and domain coverage for an institution.
    """
    inst = await InstitutionRepository.get_by_id(session, institution_id)
    if inst and not InstitutionRepository.user_can_access(inst, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: institution '{institution_id}' belongs to another user or organization.",
        )
    records = await IngestionRecordRepository.list_by_institution(session, institution_id)
    signals = await DiscoveredSignalRepository.get_by_institution(session, institution_id)
    snapshots = await SignalSnapshotRepository.get_by_institution(session, institution_id)

    all_issues: List[Dict[str, Any]] = []
    for rec in records:
        for iss in rec.data_quality_issues:
            d = iss.model_dump()
            d["ingestion_id"] = rec.ingestion_id
            d["filename"] = rec.filename
            all_issues.append(d)

    contradictory_signals = [s.model_dump(mode="json") for s in signals if s.is_contradictory]
    duplicate_signals = [s.model_dump(mode="json") for s in signals if s.is_duplicate]
    domains_discovered = sorted(list({s.domain for s in signals}))

    return {
        "institution_id": institution_id,
        "canonical_snapshot_count": len(snapshots),
        "total_discovered_signals": len(signals),
        "total_signals_count": len(snapshots) + len(signals),
        "domains_discovered": domains_discovered,
        "contradictory_signals_count": len(contradictory_signals),
        "contradictory_signals": contradictory_signals,
        "duplicate_signals_count": len(duplicate_signals),
        "duplicate_signals": duplicate_signals,
        "data_quality_issues_count": len(all_issues),
        "data_quality_issues": all_issues,
    }


@router.post("/excel", response_model=IngestionSummary)
async def ingest_excel_file(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(require_role("Auditor", "SuperAdmin"))
):
    filename = file.filename or ""
    if not filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .xlsx or .xlsm Excel files are accepted"
        )

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Uploaded Excel file exceeds the 15 MB size limit"
        )

    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        adapter = ExcelInstitutionalAdapter(tmp_path, source_id=file.filename)
        parsed = adapter.parse()

        all_signals = parsed["admissions"] + parsed["placements"] + parsed["cet_ranking"]
        if not all_signals:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="No valid institutional signals found in the uploaded workbook."
            )

        inst_id = all_signals[0].institution_id
        existing_inst = await InstitutionRepository.get_by_id(session, inst_id)
        if existing_inst and not InstitutionRepository.user_can_access(existing_inst, current_user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: institution '{inst_id}' belongs to another user or organization."
            )
        if not existing_inst:
            await InstitutionRepository.upsert(
                session,
                institution_id=inst_id,
                name=inst_id,
                organization_id=current_user.organization_id if current_user.onboarding_completed else None,
                owner_user_id=current_user.id if current_user.onboarding_completed else None,
            )
        await SignalSnapshotRepository.save_signals(session, all_signals)
        AnalysisPersistenceService.invalidate_institution(inst_id)

        return IngestionSummary(
            institution_id=inst_id,
            status="SUCCESS",
            total_signals_ingested=len(all_signals),
            admissions_count=len(parsed["admissions"]),
            placements_count=len(parsed["placements"]),
            cet_ranking_count=len(parsed["cet_ranking"])
        )
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@router.post("/synthetic", response_model=IngestionSummary)
async def ingest_synthetic_scenario(
    req: SyntheticIngestRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(require_role("Analyst", "Auditor", "SuperAdmin"))
):
    if is_deployed_environment() and current_user.role != "SuperAdmin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Synthetic scenario generation is disabled in production for non-administrative users.",
        )

    existing_inst = await InstitutionRepository.get_by_id(session, req.institution_id)
    if existing_inst and not InstitutionRepository.user_can_access(existing_inst, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: institution '{req.institution_id}' belongs to another user or organization."
        )

    raw_dict = SyntheticDataGenerator.generate_scenario(
        institution_id=req.institution_id,
        scenario_type=req.scenario,
        start_year=req.start_year,
        years_count=req.num_years
    )
    adapter = JSONDictionaryAdapter()
    parsed = adapter.parse(raw_dict)

    all_signals = parsed["admissions"] + parsed["placements"] + parsed["cet_ranking"]

    if not existing_inst:
        await InstitutionRepository.upsert(
            session,
            institution_id=req.institution_id,
            name=f"Synthetic: {req.institution_id}",
            organization_id=current_user.organization_id if current_user.onboarding_completed else None,
            owner_user_id=current_user.id if current_user.onboarding_completed else None,
        )
    await SignalSnapshotRepository.save_signals(session, all_signals)
    AnalysisPersistenceService.invalidate_institution(req.institution_id)

    return IngestionSummary(
        institution_id=req.institution_id,
        status="SUCCESS",
        total_signals_ingested=len(all_signals),
        admissions_count=len(parsed["admissions"]),
        placements_count=len(parsed["placements"]),
        cet_ranking_count=len(parsed["cet_ranking"])
    )
