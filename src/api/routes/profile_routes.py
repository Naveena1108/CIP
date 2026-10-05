"""
CIP Phase 1 Profile & Extensible Structural Hierarchy Routes.
Provides:
- User Profile (GET / PUT)
- Entity / Organization Profile (GET / PUT)
- Institution Profile & Multi-Institution Management (GET / POST / PUT)
- Child-Entity Hierarchy Management (Campuses/Schools/Faculties -> Departments -> Programs -> Data/Signals)
- Strict User, Organization, and Institution Authorization Isolation
"""

import json
import uuid
from typing import Any, Dict, List, Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_db_session
from src.db.models import InstitutionModel, OrganizationModel, HierarchyNodeModel, UserModel
from src.db.repository import (
    AccessPolicyRepository,
    DiscoveredSignalRepository,
    HierarchyNodeRepository,
    InstitutionRepository,
    OrganizationRepository,
    SignalSnapshotRepository,
    UserRepository,
)
from src.contracts import (
    AccessScopeLevel,
    UserAccessPermissionPolicy,
    AccessPermissionUpsertRequest,
)
from src.api.auth import UserResponse, get_current_user
from src.taxonomy import (
    determine_structural_archetype,
    get_applicable_profile_fields,
    is_other_value,
)

router = APIRouter(prefix="/profiles", tags=["Profiles & Structural Hierarchy"])


class SwitchContextRequest(BaseModel):
    institution_id: Optional[str] = None
    organization_id: Optional[str] = None
    view_mode: str = "INSTITUTION_VIEW"


class UserProfileUpdateRequest(BaseModel):
    full_name: Optional[str] = Field(default=None, max_length=255)
    job_title: Optional[str] = Field(default=None, max_length=128)
    phone: Optional[str] = Field(default=None, max_length=64)
    department_or_unit: Optional[str] = Field(default=None, max_length=128)
    primary_institution_id: Optional[str] = Field(default=None, max_length=64)


class OrganizationProfileUpdateRequest(BaseModel):
    org_id: Optional[str] = Field(default=None, max_length=64)
    name: str = Field(..., min_length=2, max_length=255)
    entity_category: str = Field(default="educational_group_network", max_length=64)
    entity_category_other: Optional[str] = Field(default=None, max_length=255)
    ownership_governance: str = Field(..., min_length=2, max_length=128)
    ownership_governance_other: Optional[str] = Field(default=None, max_length=255)
    education_entity_type: Optional[str] = Field(default=None, max_length=128)
    education_entity_type_other: Optional[str] = Field(default=None, max_length=255)
    university_type: Optional[str] = Field(default=None, max_length=128)
    university_type_other: Optional[str] = Field(default=None, max_length=255)
    academic_domains: Optional[List[str]] = Field(default=None)
    academic_domain_other: Optional[str] = Field(default=None, max_length=255)
    state: str = Field(default="Karnataka", max_length=64)
    city: Optional[str] = Field(default=None, max_length=128)
    website: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = Field(default=None)

    @model_validator(mode="after")
    def validate_other_fields(self) -> "OrganizationProfileUpdateRequest":
        if is_other_value(self.entity_category):
            if not self.entity_category_other or not self.entity_category_other.strip():
                raise ValueError("Please specify custom entity category (entity_category_other) when 'Other' is selected.")
            self.entity_category_other = self.entity_category_other.strip()
        if is_other_value(self.ownership_governance):
            if not self.ownership_governance_other or not self.ownership_governance_other.strip():
                raise ValueError("Please specify custom ownership/governance (ownership_governance_other) when 'Other' is selected.")
            self.ownership_governance_other = self.ownership_governance_other.strip()
        if is_other_value(self.education_entity_type):
            if not self.education_entity_type_other or not self.education_entity_type_other.strip():
                raise ValueError("Please specify custom education/entity type (education_entity_type_other) when 'Other' is selected.")
            self.education_entity_type_other = self.education_entity_type_other.strip()
        if is_other_value(self.university_type):
            if not self.university_type_other or not self.university_type_other.strip():
                raise ValueError("Please specify custom university type (university_type_other) when 'Other' is selected.")
            self.university_type_other = self.university_type_other.strip()
        if self.academic_domains and any(is_other_value(d) for d in self.academic_domains):
            if not self.academic_domain_other or not self.academic_domain_other.strip():
                raise ValueError("Please specify custom academic domain (academic_domain_other) when 'Other' is selected.")
            self.academic_domain_other = self.academic_domain_other.strip()
        return self


class InstitutionProfileRequest(BaseModel):
    institution_id: Optional[str] = Field(default=None, max_length=64)
    name: str = Field(..., min_length=2, max_length=255)
    entity_category: str = Field(default="educational_institution", max_length=64)
    entity_category_other: Optional[str] = Field(default=None, max_length=255)
    entity_type: Optional[str] = Field(default=None, max_length=64)
    entity_type_other: Optional[str] = Field(default=None, max_length=255)
    ownership_governance: str = Field(..., min_length=2, max_length=128)
    ownership_governance_other: Optional[str] = Field(default=None, max_length=255)
    education_entity_type: str = Field(..., min_length=2, max_length=128)
    education_entity_type_other: Optional[str] = Field(default=None, max_length=255)
    university_type: Optional[str] = Field(default=None, max_length=128)
    university_type_other: Optional[str] = Field(default=None, max_length=255)
    academic_domains: Optional[List[str]] = Field(default=None)
    academic_domain: Optional[str] = Field(default=None, max_length=255)
    academic_domain_other: Optional[str] = Field(default=None, max_length=255)
    parent_organization_id: Optional[str] = Field(default=None, max_length=64)
    state: str = Field(default="Karnataka", max_length=64)
    city: Optional[str] = Field(default=None, max_length=128)
    accreditation_grade: str = Field(default="A", max_length=16)
    established_year: Optional[int] = Field(default=None)
    website: Optional[str] = Field(default=None, max_length=255)
    contact_email: Optional[str] = Field(default=None, max_length=255)
    regulatory_body: Optional[str] = Field(default=None, max_length=128)
    affiliation_details: Optional[str] = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_institution_classification(self) -> "InstitutionProfileRequest":
        if is_other_value(self.entity_category) or is_other_value(self.entity_type):
            custom_cat = (self.entity_category_other or self.entity_type_other or "").strip()
            if not custom_cat:
                raise ValueError(
                    "Please specify custom entity category/type when 'Other' is selected."
                )
            self.entity_category_other = custom_cat
            self.entity_type_other = custom_cat

        if is_other_value(self.ownership_governance):
            if not self.ownership_governance_other or not self.ownership_governance_other.strip():
                raise ValueError(
                    "Please specify custom ownership/governance (ownership_governance_other) when 'Other' is selected."
                )
            self.ownership_governance_other = self.ownership_governance_other.strip()

        if is_other_value(self.education_entity_type):
            if not self.education_entity_type_other or not self.education_entity_type_other.strip():
                raise ValueError(
                    "Please specify custom education/entity type (education_entity_type_other) when 'Other' is selected."
                )
            self.education_entity_type_other = self.education_entity_type_other.strip()

        if is_other_value(self.university_type):
            if not self.university_type_other or not self.university_type_other.strip():
                raise ValueError(
                    "Please specify custom university type (university_type_other) when 'Other' is selected."
                )
            self.university_type_other = self.university_type_other.strip()

        if self.academic_domains and not self.academic_domain:
            self.academic_domain = ", ".join(self.academic_domains)
        elif self.academic_domain and not self.academic_domains:
            self.academic_domains = [d.strip() for d in self.academic_domain.split(",") if d.strip()]

        has_other_domain = is_other_value(self.academic_domain) or (
            self.academic_domains is not None and any(is_other_value(d) for d in self.academic_domains)
        )
        if has_other_domain:
            if not self.academic_domain_other or not self.academic_domain_other.strip():
                raise ValueError(
                    "Please specify custom academic domain (academic_domain_other) when 'Other' is selected."
                )
            self.academic_domain_other = self.academic_domain_other.strip()

        if not self.entity_type:
            if self.education_entity_type.strip().lower() == "university":
                self.entity_type = "university"
            elif is_other_value(self.entity_category) or is_other_value(self.education_entity_type):
                self.entity_type = "other"
                self.entity_type_other = self.entity_category_other or self.education_entity_type_other
            else:
                self.entity_type = "institution"

        return self


class HierarchyNodeRequest(BaseModel):
    node_id: Optional[str] = Field(default=None, max_length=64)
    node_type: Literal["campus_school_faculty", "department", "program", "other"]
    node_type_other: Optional[str] = Field(default=None, max_length=255)
    parent_node_id: Optional[str] = Field(default=None, max_length=64)
    code: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=2, max_length=255)
    academic_domain: Optional[str] = Field(default=None, max_length=128)
    academic_domain_other: Optional[str] = Field(default=None, max_length=255)
    degree_or_level: Optional[str] = Field(default=None, max_length=128)
    sanctioned_intake: Optional[int] = Field(default=None, ge=0)
    metadata: Optional[Dict[str, Any]] = Field(default=None)

    @model_validator(mode="after")
    def validate_node_other_fields(self) -> "HierarchyNodeRequest":
        if is_other_value(self.node_type):
            if not self.node_type_other or not self.node_type_other.strip():
                raise ValueError("Please specify custom child entity type (node_type_other) when 'other' is selected.")
            self.node_type_other = self.node_type_other.strip()
        if is_other_value(self.academic_domain):
            if not self.academic_domain_other or not self.academic_domain_other.strip():
                raise ValueError("Please specify custom academic domain (academic_domain_other) when 'Other' is selected.")
            self.academic_domain_other = self.academic_domain_other.strip()
        return self


def _serialize_institution(inst: InstitutionModel) -> Dict[str, Any]:
    domains_list: List[str] = []
    if inst.academic_domains_json:
        try:
            domains_list = json.loads(inst.academic_domains_json)
        except Exception:
            domains_list = []
    if not domains_list and inst.academic_domain:
        domains_list = [d.strip() for d in inst.academic_domain.split(",") if d.strip()]

    applicable = get_applicable_profile_fields(
        inst.entity_category, inst.entity_type, inst.education_entity_type or inst.education_level
    )
    return {
        "id": inst.id,
        "name": inst.name,
        "state": inst.state,
        "city": inst.city,
        "accreditation_grade": inst.accreditation_grade,
        "entity_category": inst.entity_category or "educational_institution",
        "entity_category_other": inst.entity_category_other,
        "entity_type": inst.entity_type or "institution",
        "entity_type_other": inst.entity_type_other,
        "ownership_governance": inst.ownership_governance,
        "ownership_governance_other": inst.ownership_governance_other,
        "education_level": inst.education_level,
        "education_entity_type": inst.education_entity_type or inst.education_level,
        "education_entity_type_other": inst.education_entity_type_other,
        "university_type": inst.university_type,
        "university_type_other": inst.university_type_other,
        "academic_domain": inst.academic_domain,
        "academic_domains": domains_list,
        "academic_domain_other": inst.academic_domain_other,
        "parent_organization_id": inst.parent_organization_id,
        "organization_id": inst.organization_id,
        "owner_user_id": inst.owner_user_id,
        "established_year": inst.established_year,
        "website": inst.website,
        "contact_email": inst.contact_email,
        "regulatory_body": inst.regulatory_body,
        "affiliation_details": inst.affiliation_details,
        "applicable_fields": applicable,
    }


def _serialize_organization(
    org: OrganizationModel,
    child_institutions: List[InstitutionModel],
    constituent_institutions: Optional[List[InstitutionModel]] = None,
) -> Dict[str, Any]:
    domains_list: List[str] = []
    if org.academic_domains_json:
        try:
            domains_list = json.loads(org.academic_domains_json)
        except Exception:
            domains_list = []

    applicable = get_applicable_profile_fields(
        org.entity_category, org.entity_category, org.education_entity_type
    )
    const_list = constituent_institutions if constituent_institutions is not None else child_institutions
    return {
        "id": org.id,
        "name": org.name,
        "entity_category": org.entity_category,
        "entity_category_other": org.entity_category_other,
        "ownership_governance": org.ownership_governance,
        "ownership_governance_other": org.ownership_governance_other,
        "education_entity_type": org.education_entity_type,
        "education_entity_type_other": org.education_entity_type_other,
        "university_type": org.university_type,
        "university_type_other": org.university_type_other,
        "academic_domains": domains_list,
        "academic_domain_other": org.academic_domain_other,
        "state": org.state,
        "city": org.city,
        "website": org.website,
        "description": org.description,
        "owner_user_id": org.owner_user_id,
        "applicable_fields": applicable,
        "institutions": [_serialize_institution(i) for i in child_institutions],
        "constituent_institutions": [_serialize_institution(i) for i in const_list],
        "constituent_institution_count": len(const_list),
    }


def _serialize_node(node: HierarchyNodeModel) -> Dict[str, Any]:
    meta = None
    if node.metadata_json:
        try:
            meta = json.loads(node.metadata_json)
        except Exception:
            meta = None
    return {
        "id": node.id,
        "institution_id": node.institution_id,
        "organization_id": node.organization_id,
        "parent_node_id": node.parent_node_id,
        "node_type": node.node_type,
        "node_type_other": node.node_type_other,
        "code": node.code,
        "name": node.name,
        "academic_domain": node.academic_domain,
        "academic_domain_other": node.academic_domain_other,
        "degree_or_level": node.degree_or_level,
        "sanctioned_intake": node.sanctioned_intake,
        "metadata": meta,
        "owner_user_id": node.owner_user_id,
    }


@router.get("/permissions", response_model=UserAccessPermissionPolicy)
async def get_my_access_permissions(
    current_user: UserModel = Depends(get_current_user),
):
    """Return the active 4-tier access permission policy for the current user."""
    return AccessPolicyRepository.get_policy(current_user)


@router.post("/permissions", response_model=UserAccessPermissionPolicy)
@router.put("/permissions", response_model=UserAccessPermissionPolicy)
async def configure_access_permissions(
    req: AccessPermissionUpsertRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """
    Configure or update 4-tier permissions (user, organization, institution, department_program)
    for a target user (by target_user_id or target_email, or current user if both are omitted).
    """
    target_user = current_user
    if req.target_user_id and req.target_user_id != current_user.id:
        target_user = await UserRepository.get_by_id(session, req.target_user_id)
        if not target_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Target user '{req.target_user_id}' not found.",
            )
    elif req.target_email and req.target_email.strip().lower() != (current_user.email or "").lower():
        target_user = await UserRepository.get_by_email(session, req.target_email.strip())
        if not target_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Target user with email '{req.target_email}' not found.",
            )

    if target_user.id != current_user.id:
        # Verify caller has authority over the organization
        org_to_check = req.organization_id or current_user.organization_id
        if current_user.role != "SuperAdmin":
            if not org_to_check:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: only organization owners or SuperAdmin can assign permissions to other users.",
                )
            org_model = await OrganizationRepository.get_by_id(session, org_to_check)
            if not org_model or not OrganizationRepository.user_can_access(org_model, current_user):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: you do not have organization-level authority for this organization.",
                )

    policy = await AccessPolicyRepository.set_policy(
        session=session,
        user=target_user,
        scope_level=req.scope_level or AccessScopeLevel.ORGANIZATION,
        organization_id=req.organization_id,
        allowed_institution_ids=req.allowed_institution_ids,
        allowed_departments_programs=req.allowed_departments_programs,
    )
    return policy


@router.post("/switch-context")
@router.post("/switch-institution")
async def switch_active_context(
    req: SwitchContextRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """
    Switch the user's active context between Organization/Network View and Institution View,
    or switch the active constituent institution within the organization.
    """
    policy = AccessPolicyRepository.get_policy(current_user)
    raw_vm = (req.view_mode or "INSTITUTION_VIEW").strip()
    is_org_view = raw_vm.upper() in ("ORGANIZATION_NETWORK_VIEW", "ORGANIZATION", "NETWORK", "ORGANIZATION_VIEW")

    if is_org_view:
        org_id = req.organization_id or current_user.organization_id
        if not org_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No organization context is associated with this user.",
            )
        org = await OrganizationRepository.get_by_id(session, org_id)
        if org and not OrganizationRepository.user_can_access(org, current_user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: your permission scope does not allow viewing organization-wide network intelligence.",
            )
        if not policy.can_view_organization_network:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: institution-scoped or department-scoped users cannot switch to Organization/Network View.",
            )
        return {
            "status": "CONTEXT_SWITCHED",
            "view_mode": raw_vm if raw_vm.islower() else "ORGANIZATION_NETWORK_VIEW",
            "view_type": "ORGANIZATION_NETWORK_VIEW",
            "organization_id": org_id,
            "active_organization_id": org_id,
            "primary_institution_id": current_user.primary_institution_id,
            "active_institution_id": current_user.primary_institution_id,
            "access_policy": policy.model_dump(),
        }

    # Institution view switch
    target_inst_id = req.institution_id or current_user.primary_institution_id
    if not target_inst_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please provide institution_id when switching to INSTITUTION_VIEW.",
        )
    inst = await InstitutionRepository.get_by_id(session, target_inst_id)
    if not inst:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Institution '{target_inst_id}' not found.",
        )
    if not InstitutionRepository.user_can_access(inst, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: you do not have permission to access institution '{target_inst_id}'.",
        )

    current_user.primary_institution_id = inst.id
    if inst.organization_id and not current_user.organization_id:
        current_user.organization_id = inst.organization_id
    await session.flush()

    resolved_org_id = inst.organization_id or inst.parent_organization_id or current_user.organization_id
    return {
        "status": "CONTEXT_SWITCHED",
        "view_mode": raw_vm if raw_vm.islower() else "INSTITUTION_VIEW",
        "view_type": "INSTITUTION_VIEW",
        "organization_id": resolved_org_id,
        "active_organization_id": resolved_org_id,
        "primary_institution_id": inst.id,
        "active_institution_id": inst.id,
        "institution": _serialize_institution(inst),
        "access_policy": AccessPolicyRepository.get_policy(current_user).model_dump(),
    }


@router.get("/user", response_model=UserResponse)
async def get_user_profile(current_user: UserModel = Depends(get_current_user)):
    onboarding_done = bool(current_user.onboarding_completed)
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        role=current_user.role,
        is_active=current_user.is_active,
        auth_provider=current_user.auth_provider or "local",
        full_name=current_user.full_name,
        job_title=current_user.job_title,
        phone=current_user.phone,
        department_or_unit=current_user.department_or_unit,
        organization_id=current_user.organization_id,
        primary_institution_id=current_user.primary_institution_id,
        onboarding_completed=onboarding_done,
        onboarding_required=not onboarding_done,
        access_policy=AccessPolicyRepository.get_policy(current_user).model_dump(),
    )


@router.put("/user", response_model=UserResponse)
async def update_user_profile(
    req: UserProfileUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    if req.primary_institution_id:
        inst = await InstitutionRepository.get_by_id(session, req.primary_institution_id)
        if not inst:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Institution '{req.primary_institution_id}' not found.",
            )
        if not InstitutionRepository.user_can_access(inst, current_user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: cannot switch primary institution to another organization's entity.",
            )

    updated = await UserRepository.update_profile(
        session=session,
        user=current_user,
        full_name=req.full_name,
        job_title=req.job_title,
        phone=req.phone,
        department_or_unit=req.department_or_unit,
        primary_institution_id=req.primary_institution_id,
    )
    onboarding_done = bool(updated.onboarding_completed)
    return UserResponse(
        id=updated.id,
        email=updated.email,
        role=updated.role,
        is_active=updated.is_active,
        auth_provider=updated.auth_provider or "local",
        full_name=updated.full_name,
        job_title=updated.job_title,
        phone=updated.phone,
        department_or_unit=updated.department_or_unit,
        organization_id=updated.organization_id,
        primary_institution_id=updated.primary_institution_id,
        onboarding_completed=onboarding_done,
        onboarding_required=not onboarding_done,
        access_policy=AccessPolicyRepository.get_policy(updated).model_dump(),
    )


@router.get("/organization")
async def get_my_organization_profile(
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    org_id = current_user.organization_id or current_user.primary_institution_id
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No organization or entity profile is linked to this user yet. Complete onboarding first.",
        )

    org = await OrganizationRepository.get_by_id(session, org_id)
    child_insts = await InstitutionRepository.list_by_organization(session, org_id)
    accessible_children = [i for i in child_insts if InstitutionRepository.user_can_access(i, current_user)]
    constituent_insts = await InstitutionRepository.list_constituent_institutions(session, org_id, current_user)

    if not org:
        # Synthesize organization view from primary institution if user onboarded directly as an institution
        primary_inst = await InstitutionRepository.get_by_id(session, org_id)
        if not primary_inst and current_user.primary_institution_id:
            primary_inst = await InstitutionRepository.get_by_id(session, current_user.primary_institution_id)
        if not primary_inst or not InstitutionRepository.user_can_access(primary_inst, current_user):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Organization profile '{org_id}' not found.",
            )
        org = await OrganizationRepository.upsert(
            session=session,
            org_id=org_id,
            name=org_id if org_id != primary_inst.id else primary_inst.name,
            entity_category=primary_inst.entity_category or "educational_institution",
            entity_category_other=primary_inst.entity_category_other,
            ownership_governance=primary_inst.ownership_governance,
            ownership_governance_other=primary_inst.ownership_governance_other,
            education_entity_type=primary_inst.education_entity_type or primary_inst.education_level,
            education_entity_type_other=primary_inst.education_entity_type_other,
            university_type=primary_inst.university_type,
            university_type_other=primary_inst.university_type_other,
            academic_domains=json.loads(primary_inst.academic_domains_json) if primary_inst.academic_domains_json else None,
            academic_domain_other=primary_inst.academic_domain_other,
            state=primary_inst.state,
            city=primary_inst.city,
            website=primary_inst.website,
            owner_user_id=primary_inst.owner_user_id or current_user.id,
        )
        if not accessible_children:
            accessible_children = [primary_inst]
        if not constituent_insts:
            constituent_insts = [primary_inst]

    if not OrganizationRepository.user_can_access(org, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: organization belongs to another user or exceeds your permission scope.",
        )

    return _serialize_organization(org, accessible_children, constituent_insts)


@router.put("/organization")
async def update_my_organization_profile(
    req: OrganizationProfileUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    target_org_id = req.org_id or current_user.organization_id or current_user.primary_institution_id
    if not target_org_id:
        target_org_id = f"ORG_{uuid.uuid4().hex[:8].upper()}"

    existing = await OrganizationRepository.get_by_id(session, target_org_id)
    if existing and not OrganizationRepository.user_can_access(existing, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: cannot modify another user's organization profile.",
        )

    org = await OrganizationRepository.upsert(
        session=session,
        org_id=target_org_id,
        name=req.name,
        entity_category=req.entity_category,
        entity_category_other=req.entity_category_other,
        ownership_governance=req.ownership_governance,
        ownership_governance_other=req.ownership_governance_other,
        education_entity_type=req.education_entity_type,
        education_entity_type_other=req.education_entity_type_other,
        university_type=req.university_type,
        university_type_other=req.university_type_other,
        academic_domains=req.academic_domains,
        academic_domain_other=req.academic_domain_other,
        state=req.state,
        city=req.city,
        website=req.website,
        description=req.description,
        owner_user_id=current_user.id,
    )
    if not current_user.organization_id:
        current_user.organization_id = org.id
        await session.flush()

    child_insts = await InstitutionRepository.list_by_organization(session, org.id)
    accessible_children = [i for i in child_insts if InstitutionRepository.user_can_access(i, current_user)]
    return _serialize_organization(org, accessible_children)


@router.get("/institutions")
async def list_accessible_institution_profiles(
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    insts = await InstitutionRepository.list_for_user(session, current_user)
    return [_serialize_institution(i) for i in insts]


@router.post("/institutions")
async def create_child_or_standalone_institution(
    req: InstitutionProfileRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """
    Create a new institution (either standalone or under the user's organization/group).
    Supports multiple institutions of different types per user/organization.
    """
    inst_id = (req.institution_id or "").strip() or f"INST_{uuid.uuid4().hex[:8].upper()}"
    existing = await InstitutionRepository.get_by_id(session, inst_id)
    if existing and not InstitutionRepository.user_can_access(existing, current_user):
        if not (current_user.primary_institution_id == inst_id or (current_user.organization_id and existing.organization_id == current_user.organization_id)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: institution ID '{inst_id}' is already owned by another user or organization.",
            )

    parent_org_id = req.parent_organization_id or current_user.organization_id
    if parent_org_id:
        parent_org = await OrganizationRepository.get_by_id(session, parent_org_id)
        if parent_org and not OrganizationRepository.user_can_access(parent_org, current_user):
            if current_user.organization_id != parent_org_id and current_user.primary_institution_id != parent_org_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: parent organization belongs to another user.",
                )

    org_id = parent_org_id or inst_id
    inst = await InstitutionRepository.upsert(
        session=session,
        institution_id=inst_id,
        name=req.name,
        state=req.state,
        city=req.city,
        grade=req.accreditation_grade,
        entity_category=req.entity_category,
        entity_category_other=req.entity_category_other,
        entity_type=req.entity_type,
        entity_type_other=req.entity_type_other,
        ownership_governance=req.ownership_governance,
        ownership_governance_other=req.ownership_governance_other,
        education_level=req.education_entity_type,
        education_entity_type=req.education_entity_type,
        education_entity_type_other=req.education_entity_type_other,
        university_type=req.university_type,
        university_type_other=req.university_type_other,
        academic_domain=req.academic_domain,
        academic_domains=req.academic_domains,
        academic_domain_other=req.academic_domain_other,
        parent_organization_id=parent_org_id,
        organization_id=org_id,
        owner_user_id=current_user.id,
        established_year=req.established_year,
        website=req.website,
        contact_email=req.contact_email,
        regulatory_body=req.regulatory_body,
        affiliation_details=req.affiliation_details,
    )

    if not current_user.primary_institution_id:
        current_user.primary_institution_id = inst.id
    if not current_user.organization_id:
        current_user.organization_id = org_id
    await session.flush()

    return _serialize_institution(inst)


@router.get("/institutions/{institution_id}")
async def get_institution_profile(
    institution_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    inst = await InstitutionRepository.get_by_id(session, institution_id)
    if not inst:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Institution '{institution_id}' not found.",
        )
    if not InstitutionRepository.user_can_access(inst, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: institution '{institution_id}' belongs to another user or organization.",
        )
    return _serialize_institution(inst)


@router.put("/institutions/{institution_id}")
async def update_institution_profile(
    institution_id: str,
    req: InstitutionProfileRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    inst = await InstitutionRepository.get_by_id(session, institution_id)
    if not inst:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Institution '{institution_id}' not found.",
        )
    if not InstitutionRepository.user_can_access(inst, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: institution '{institution_id}' belongs to another user or organization.",
        )

    updated = await InstitutionRepository.upsert(
        session=session,
        institution_id=institution_id,
        name=req.name,
        state=req.state,
        city=req.city,
        grade=req.accreditation_grade,
        entity_category=req.entity_category,
        entity_category_other=req.entity_category_other,
        entity_type=req.entity_type,
        entity_type_other=req.entity_type_other,
        ownership_governance=req.ownership_governance,
        ownership_governance_other=req.ownership_governance_other,
        education_level=req.education_entity_type,
        education_entity_type=req.education_entity_type,
        education_entity_type_other=req.education_entity_type_other,
        university_type=req.university_type,
        university_type_other=req.university_type_other,
        academic_domain=req.academic_domain,
        academic_domains=req.academic_domains,
        academic_domain_other=req.academic_domain_other,
        parent_organization_id=req.parent_organization_id or inst.parent_organization_id,
        organization_id=inst.organization_id or current_user.organization_id or institution_id,
        owner_user_id=inst.owner_user_id or current_user.id,
        established_year=req.established_year,
        website=req.website,
        contact_email=req.contact_email,
        regulatory_body=req.regulatory_body,
        affiliation_details=req.affiliation_details,
    )
    return _serialize_institution(updated)


@router.get("/institutions/{institution_id}/structure")
async def get_institution_hierarchical_structure(
    institution_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """
    Retrieve the complete hierarchical tree for an entity:
    - Organization/Group -> Institutions -> Departments -> Programs -> Data/Signals
    - University -> Campuses/Schools/Faculties -> Departments -> Programs -> Data/Signals
    - Institution -> Departments -> Programs -> Data/Signals
    Also includes auto-discovered departments from ingested signal snapshots and universal discovered signals.
    Respects user, organization, institution, and department/program permissions.
    """
    inst = await InstitutionRepository.get_by_id(session, institution_id)
    if not inst:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Institution '{institution_id}' not found.",
        )
    if not InstitutionRepository.user_can_access(inst, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: institution '{institution_id}' belongs to another user or organization.",
        )

    nodes = await HierarchyNodeRepository.list_by_institution(session, institution_id)
    snapshots = await SignalSnapshotRepository.get_by_institution(session, institution_id)
    dynamic_signals = await DiscoveredSignalRepository.get_by_institution(session, institution_id)

    allowed_depts = AccessPolicyRepository.get_allowed_departments_filter(current_user, institution_id)
    if allowed_depts is not None:
        allowed_set = set(allowed_depts)
        nodes = [
            n for n in nodes
            if n.node_type == "campus_school_faculty"
            or (n.code or "").strip().upper() in allowed_set
            or (n.name or "").strip().upper() in allowed_set
        ]
        snapshots = [s for s in snapshots if (s.department or "").strip().upper() in allowed_set]
        dynamic_signals = [
            ds for ds in dynamic_signals
            if (ds.context.department or "").strip().upper() in allowed_set
            or (ds.context.program or "").strip().upper() in allowed_set
        ]

    campuses_schools_faculties = [_serialize_node(n) for n in nodes if n.node_type == "campus_school_faculty"]
    departments = [_serialize_node(n) for n in nodes if n.node_type == "department"]
    programs = [_serialize_node(n) for n in nodes if n.node_type == "program"]
    custom_nodes = [_serialize_node(n) for n in nodes if n.node_type == "other"]

    # Summarize signals by department and auto-surface any departments present in signal snapshots or dynamic signals
    dept_signal_counts: Dict[str, Dict[str, int]] = {}
    for snap in snapshots:
        d_code = snap.department
        if d_code not in dept_signal_counts:
            dept_signal_counts[d_code] = {"admissions": 0, "placements": 0, "cet_ranking": 0, "dynamic": 0, "total": 0}
        if snap.signal_type in dept_signal_counts[d_code]:
            dept_signal_counts[d_code][snap.signal_type] += 1
        dept_signal_counts[d_code]["total"] += 1

    for ds in dynamic_signals:
        d_code = ds.context.department or ds.context.program or "GENERAL"
        if d_code not in dept_signal_counts:
            dept_signal_counts[d_code] = {"admissions": 0, "placements": 0, "cet_ranking": 0, "dynamic": 0, "total": 0}
        dept_signal_counts[d_code]["dynamic"] = dept_signal_counts[d_code].get("dynamic", 0) + 1
        dept_signal_counts[d_code]["total"] += 1

    known_dept_codes = {d["code"] for d in departments}
    for d_code in sorted(dept_signal_counts.keys()):
        if d_code not in known_dept_codes:
            departments.append(
                {
                    "id": f"auto_dept_{institution_id}_{d_code}",
                    "institution_id": institution_id,
                    "organization_id": inst.organization_id,
                    "parent_node_id": None,
                    "node_type": "department",
                    "node_type_other": None,
                    "code": d_code,
                    "name": f"Department of {d_code}",
                    "academic_domain": inst.academic_domain,
                    "academic_domain_other": inst.academic_domain_other,
                    "degree_or_level": inst.education_entity_type or inst.education_level,
                    "sanctioned_intake": None,
                    "metadata": {"source": "signal_snapshots"},
                    "owner_user_id": inst.owner_user_id,
                }
            )

    org_summary = None
    org_institutions: List[Dict[str, Any]] = []
    org_lookup_id = inst.organization_id or inst.parent_organization_id
    if org_lookup_id:
        org_model = await OrganizationRepository.get_by_id(session, org_lookup_id)
        sibling_insts = await InstitutionRepository.list_by_organization(session, org_lookup_id)
        accessible_siblings = [
            _serialize_institution(s) for s in sibling_insts if InstitutionRepository.user_can_access(s, current_user)
        ]
        org_institutions = accessible_siblings
        if org_model and OrganizationRepository.user_can_access(org_model, current_user):
            org_summary = {
                "id": org_model.id,
                "name": org_model.name,
                "entity_category": org_model.entity_category,
                "entity_category_other": org_model.entity_category_other,
                "education_entity_type": org_model.education_entity_type,
                "education_entity_type_other": org_model.education_entity_type_other,
                "ownership_governance": org_model.ownership_governance,
                "ownership_governance_other": org_model.ownership_governance_other,
            }

    archetype = determine_structural_archetype(
        inst.entity_category, inst.entity_type, inst.education_entity_type or inst.education_level
    )

    return {
        "institution_id": institution_id,
        "organization_id": org_lookup_id,
        "structural_archetype": archetype,
        "organization": org_summary,
        "organization_institutions": org_institutions,
        "institution": _serialize_institution(inst),
        "campuses_schools_faculties": campuses_schools_faculties,
        "departments": departments,
        "programs": programs,
        "custom_nodes": custom_nodes,
        "access_policy": AccessPolicyRepository.get_policy(current_user).model_dump(),
        "signals_summary": {
            "total_snapshots": len(snapshots),
            "total_dynamic_signals": len(dynamic_signals),
            "total_signals": len(snapshots) + len(dynamic_signals),
            "by_department": dept_signal_counts,
        },
    }


@router.post("/institutions/{institution_id}/nodes")
@router.post("/institutions/{institution_id}/hierarchy")
async def create_or_update_hierarchy_node(
    institution_id: str,
    req: HierarchyNodeRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """
    Add or update a child structural node (Campus/School/Faculty, Department, Program, or custom Other)
    under the specified institution.
    """
    inst = await InstitutionRepository.get_by_id(session, institution_id)
    if not inst:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Institution '{institution_id}' not found.",
        )
    if not InstitutionRepository.user_can_access(inst, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: institution '{institution_id}' belongs to another user or organization.",
        )

    if req.node_type in ("department", "program"):
        if not AccessPolicyRepository.user_can_access_department(current_user, institution_id, req.code):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: your department/program permission scope does not permit modifying '{req.code}'.",
            )

    if req.parent_node_id:
        parent_node = await HierarchyNodeRepository.get_by_id(session, req.parent_node_id)
        if not parent_node or parent_node.institution_id != institution_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Parent node '{req.parent_node_id}' does not belong to institution '{institution_id}'.",
            )

    node_id = (req.node_id or "").strip() or f"node_{institution_id}_{req.node_type[:4]}_{req.code}_{uuid.uuid4().hex[:6]}"
    node = await HierarchyNodeRepository.upsert(
        session=session,
        node_id=node_id,
        institution_id=institution_id,
        organization_id=inst.organization_id or inst.parent_organization_id,
        parent_node_id=req.parent_node_id,
        node_type=req.node_type,
        node_type_other=req.node_type_other,
        code=req.code.strip(),
        name=req.name.strip(),
        academic_domain=req.academic_domain,
        academic_domain_other=req.academic_domain_other,
        degree_or_level=req.degree_or_level,
        sanctioned_intake=req.sanctioned_intake,
        metadata=req.metadata,
        owner_user_id=current_user.id,
    )
    return _serialize_node(node)


@router.delete("/nodes/{node_id}")
async def delete_hierarchy_node(
    node_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    node = await HierarchyNodeRepository.get_by_id(session, node_id)
    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Hierarchy node '{node_id}' not found.",
        )
    inst = await InstitutionRepository.get_by_id(session, node.institution_id)
    if inst and not InstitutionRepository.user_can_access(inst, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: node belongs to another user or organization.",
        )
    if node.node_type in ("department", "program") and not AccessPolicyRepository.user_can_access_department(
        current_user, node.institution_id, node.code
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: your department/program permission scope does not permit deleting '{node.code}'.",
        )
    await HierarchyNodeRepository.delete_by_id(session, node_id)
    return {"status": "DELETED", "node_id": node_id}
