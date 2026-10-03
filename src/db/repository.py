"""
Database Repositories for AI CRISS CRUD Operations.
Provides high-level async methods bridging Pydantic contracts and SQLAlchemy ORM models.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import select, desc, delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    OrganizationModel,
    InstitutionModel,
    HierarchyNodeModel,
    SignalSnapshotModel,
    CrisisAssessmentModel,
    UserModel,
    OTPVerificationModel,
    RevokedTokenModel,
    DiscoveredSignalModel,
    IngestionRecordModel,
    InstitutionalMemoryEntryModel,
)
from src.contracts import (
    CrisisAssessment,
    AdmissionsSignal,
    PlacementsSignal,
    CETRankingSignal,
    DiscoveredSignal,
    UniversalIngestionResult,
    MemoryCategory,
    FeedbackVerdict,
    InstitutionalMemoryEntry,
    UserFeedbackSubmission,
    UserFeedbackRecord,
    AccessScopeLevel,
    UserAccessPermissionPolicy,
)


_USER_ACCESS_POLICIES: Dict[str, UserAccessPermissionPolicy] = {}


class AccessPolicyRepository:
    """
    Manages 4-tier granular access control for CIP Phase 7:
    - user
    - organization
    - institution
    - department_program
    """

    @staticmethod
    def clear_all() -> None:
        _USER_ACCESS_POLICIES.clear()

    @staticmethod
    async def set_policy(
        session: AsyncSession,
        user: UserModel,
        scope_level: AccessScopeLevel,
        organization_id: Optional[str] = None,
        allowed_institution_ids: Optional[List[str]] = None,
        allowed_departments_programs: Optional[List[str]] = None,
    ) -> UserAccessPermissionPolicy:
        inst_ids = list(allowed_institution_ids or [])
        dept_codes = [d.strip() for d in (allowed_departments_programs or []) if d and d.strip()]
        resolved_org_id = organization_id if organization_id is not None else user.organization_id

        if scope_level == AccessScopeLevel.ORGANIZATION:
            can_view_org = True
            desc_str = f"Organization/network-wide access for organization '{resolved_org_id or '*'}'."
        elif scope_level == AccessScopeLevel.INSTITUTION:
            if not inst_ids and user.primary_institution_id:
                inst_ids = [user.primary_institution_id]
            can_view_org = False
            desc_str = f"Institution-scoped access restricted to: {', '.join(inst_ids) or 'none'}."
        elif scope_level == AccessScopeLevel.DEPARTMENT_PROGRAM:
            if not inst_ids and user.primary_institution_id:
                inst_ids = [user.primary_institution_id]
            can_view_org = False
            desc_str = (
                f"Department/program-scoped access restricted to institution(s) "
                f"[{', '.join(inst_ids)}] and department(s)/program(s) [{', '.join(dept_codes)}]."
            )
        else:
            can_view_org = False
            desc_str = f"Isolated user-level workspace access for user '{user.id}'."

        policy = UserAccessPermissionPolicy(
            user_id=user.id,
            scope_level=scope_level,
            organization_id=resolved_org_id,
            allowed_institution_ids=inst_ids,
            allowed_departments_programs=dept_codes,
            can_view_organization_network=can_view_org,
            description=desc_str,
        )
        _USER_ACCESS_POLICIES[user.id] = policy

        if resolved_org_id is not None:
            user.organization_id = resolved_org_id
        if inst_ids:
            user.primary_institution_id = inst_ids[0]
        if dept_codes and scope_level == AccessScopeLevel.DEPARTMENT_PROGRAM:
            user.department_or_unit = ", ".join(dept_codes)
        await session.flush()
        return policy

    @staticmethod
    def get_policy(user: UserModel) -> UserAccessPermissionPolicy:
        if user.id in _USER_ACCESS_POLICIES:
            return _USER_ACCESS_POLICIES[user.id]

        if user.role == "SuperAdmin":
            return UserAccessPermissionPolicy(
                user_id=user.id,
                scope_level=AccessScopeLevel.ORGANIZATION,
                organization_id=user.organization_id,
                allowed_institution_ids=[],
                allowed_departments_programs=[],
                can_view_organization_network=True,
                description="SuperAdmin unrestricted access",
            )

        if user.organization_id:
            return UserAccessPermissionPolicy(
                user_id=user.id,
                scope_level=AccessScopeLevel.ORGANIZATION,
                organization_id=user.organization_id,
                allowed_institution_ids=[],
                allowed_departments_programs=[],
                can_view_organization_network=True,
                description=f"Organization/network access for {user.organization_id}",
            )

        if user.primary_institution_id:
            return UserAccessPermissionPolicy(
                user_id=user.id,
                scope_level=AccessScopeLevel.INSTITUTION,
                organization_id=None,
                allowed_institution_ids=[user.primary_institution_id],
                allowed_departments_programs=[],
                can_view_organization_network=False,
                description=f"Institution access for {user.primary_institution_id}",
            )

        return UserAccessPermissionPolicy(
            user_id=user.id,
            scope_level=AccessScopeLevel.USER,
            organization_id=None,
            allowed_institution_ids=[],
            allowed_departments_programs=[],
            can_view_organization_network=False,
            description=f"User workspace access for {user.id}",
        )

    @staticmethod
    def user_can_access_department(
        user: UserModel,
        institution_id: str,
        department_or_program: Optional[str],
    ) -> bool:
        if user.role == "SuperAdmin":
            return True
        policy = AccessPolicyRepository.get_policy(user)
        if policy.scope_level != AccessScopeLevel.DEPARTMENT_PROGRAM:
            return True
        if policy.allowed_institution_ids and institution_id not in policy.allowed_institution_ids:
            return False
        if not department_or_program:
            return True
        allowed_upper = {d.strip().upper() for d in policy.allowed_departments_programs if d}
        target_upper = department_or_program.strip().upper()
        return target_upper in allowed_upper

    @staticmethod
    def get_allowed_departments_filter(
        user: UserModel,
        institution_id: str,
    ) -> Optional[List[str]]:
        if user.role == "SuperAdmin":
            return None
        policy = AccessPolicyRepository.get_policy(user)
        if policy.scope_level != AccessScopeLevel.DEPARTMENT_PROGRAM:
            return None
        if policy.allowed_institution_ids and institution_id not in policy.allowed_institution_ids:
            return []
        return [d.strip().upper() for d in policy.allowed_departments_programs if d]


class OrganizationRepository:
    @staticmethod
    async def upsert(
        session: AsyncSession,
        org_id: str,
        name: str,
        entity_category: str = "educational_institution",
        entity_category_other: Optional[str] = None,
        ownership_governance: Optional[str] = None,
        ownership_governance_other: Optional[str] = None,
        education_entity_type: Optional[str] = None,
        education_entity_type_other: Optional[str] = None,
        university_type: Optional[str] = None,
        university_type_other: Optional[str] = None,
        academic_domains: Optional[List[str]] = None,
        academic_domain_other: Optional[str] = None,
        state: str = "Karnataka",
        city: Optional[str] = None,
        website: Optional[str] = None,
        description: Optional[str] = None,
        owner_user_id: Optional[str] = None,
    ) -> OrganizationModel:
        stmt = select(OrganizationModel).where(OrganizationModel.id == org_id)
        org = (await session.execute(stmt)).scalar_one_or_none()
        domains_json = json.dumps(academic_domains) if academic_domains is not None else None
        if not org:
            org = OrganizationModel(
                id=org_id,
                name=name,
                entity_category=entity_category,
                entity_category_other=entity_category_other,
                ownership_governance=ownership_governance,
                ownership_governance_other=ownership_governance_other,
                education_entity_type=education_entity_type,
                education_entity_type_other=education_entity_type_other,
                university_type=university_type,
                university_type_other=university_type_other,
                academic_domains_json=domains_json,
                academic_domain_other=academic_domain_other,
                state=state,
                city=city,
                website=website,
                description=description,
                owner_user_id=owner_user_id,
            )
            session.add(org)
        else:
            org.name = name
            if entity_category is not None:
                org.entity_category = entity_category
            org.entity_category_other = entity_category_other
            if ownership_governance is not None:
                org.ownership_governance = ownership_governance
            org.ownership_governance_other = ownership_governance_other
            if education_entity_type is not None:
                org.education_entity_type = education_entity_type
            org.education_entity_type_other = education_entity_type_other
            if university_type is not None:
                org.university_type = university_type
            org.university_type_other = university_type_other
            if domains_json is not None:
                org.academic_domains_json = domains_json
            org.academic_domain_other = academic_domain_other
            if state is not None:
                org.state = state
            if city is not None:
                org.city = city
            if website is not None:
                org.website = website
            if description is not None:
                org.description = description
            if owner_user_id is not None and org.owner_user_id is None:
                org.owner_user_id = owner_user_id
        await session.flush()
        return org

    @staticmethod
    async def get_by_id(session: AsyncSession, org_id: str) -> Optional[OrganizationModel]:
        stmt = select(OrganizationModel).where(OrganizationModel.id == org_id)
        return (await session.execute(stmt)).scalar_one_or_none()

    @staticmethod
    def user_can_access(org: OrganizationModel, user: UserModel) -> bool:
        if user.role == "SuperAdmin":
            return True
        if user.id in _USER_ACCESS_POLICIES:
            policy = _USER_ACCESS_POLICIES[user.id]
            if not policy.can_view_organization_network:
                return False
            if policy.organization_id and policy.organization_id == org.id:
                return True
            return False
        if org.owner_user_id and org.owner_user_id == user.id:
            return True
        if user.organization_id and org.id == user.organization_id:
            return True
        return False


class InstitutionRepository:
    @staticmethod
    async def upsert(
        session: AsyncSession,
        institution_id: str,
        name: str,
        state: str = "Karnataka",
        grade: str = "A",
        city: Optional[str] = None,
        entity_category: Optional[str] = None,
        entity_category_other: Optional[str] = None,
        entity_type: Optional[str] = None,
        entity_type_other: Optional[str] = None,
        ownership_governance: Optional[str] = None,
        ownership_governance_other: Optional[str] = None,
        education_level: Optional[str] = None,
        education_entity_type: Optional[str] = None,
        education_entity_type_other: Optional[str] = None,
        university_type: Optional[str] = None,
        university_type_other: Optional[str] = None,
        academic_domain: Optional[str] = None,
        academic_domains: Optional[List[str]] = None,
        academic_domain_other: Optional[str] = None,
        parent_organization_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        owner_user_id: Optional[str] = None,
        established_year: Optional[int] = None,
        website: Optional[str] = None,
        contact_email: Optional[str] = None,
        regulatory_body: Optional[str] = None,
        affiliation_details: Optional[str] = None,
    ) -> InstitutionModel:
        stmt = select(InstitutionModel).where(InstitutionModel.id == institution_id)
        result = await session.execute(stmt)
        inst = result.scalar_one_or_none()
        domains_json = json.dumps(academic_domains) if academic_domains is not None else None
        if academic_domain is None and academic_domains:
            academic_domain = ", ".join(academic_domains)
        if education_level is None and education_entity_type:
            education_level = education_entity_type

        if not inst:
            inst = InstitutionModel(
                id=institution_id,
                name=name,
                state=state,
                city=city,
                accreditation_grade=grade,
                entity_category=entity_category or "educational_institution",
                entity_category_other=entity_category_other,
                entity_type=entity_type or "institution",
                entity_type_other=entity_type_other,
                ownership_governance=ownership_governance,
                ownership_governance_other=ownership_governance_other,
                education_level=education_level,
                education_entity_type=education_entity_type,
                education_entity_type_other=education_entity_type_other,
                university_type=university_type,
                university_type_other=university_type_other,
                academic_domain=academic_domain,
                academic_domains_json=domains_json,
                academic_domain_other=academic_domain_other,
                parent_organization_id=parent_organization_id,
                organization_id=organization_id,
                owner_user_id=owner_user_id,
                established_year=established_year,
                website=website,
                contact_email=contact_email,
                regulatory_body=regulatory_body,
                affiliation_details=affiliation_details,
            )
            session.add(inst)
        else:
            inst.name = name
            inst.state = state
            inst.accreditation_grade = grade
            if city is not None:
                inst.city = city
            if entity_category is not None:
                inst.entity_category = entity_category
            if entity_category_other is not None or (entity_category and entity_category != "other"):
                inst.entity_category_other = entity_category_other
            if entity_type is not None:
                inst.entity_type = entity_type
            if entity_type_other is not None or (entity_type and entity_type != "other"):
                inst.entity_type_other = entity_type_other
            if ownership_governance is not None:
                inst.ownership_governance = ownership_governance
            if ownership_governance_other is not None or (ownership_governance and ownership_governance.lower() != "other"):
                inst.ownership_governance_other = ownership_governance_other
            if education_level is not None:
                inst.education_level = education_level
            if education_entity_type is not None:
                inst.education_entity_type = education_entity_type
            if education_entity_type_other is not None or (education_entity_type and education_entity_type.lower() != "other"):
                inst.education_entity_type_other = education_entity_type_other
            if university_type is not None:
                inst.university_type = university_type
            if university_type_other is not None or (university_type and university_type.lower() != "other"):
                inst.university_type_other = university_type_other
            if academic_domain is not None:
                inst.academic_domain = academic_domain
            if domains_json is not None:
                inst.academic_domains_json = domains_json
            if academic_domain_other is not None:
                inst.academic_domain_other = academic_domain_other
            if parent_organization_id is not None:
                inst.parent_organization_id = parent_organization_id
            if organization_id is not None:
                inst.organization_id = organization_id
            if owner_user_id is not None and inst.owner_user_id is None:
                inst.owner_user_id = owner_user_id
            if established_year is not None:
                inst.established_year = established_year
            if website is not None:
                inst.website = website
            if contact_email is not None:
                inst.contact_email = contact_email
            if regulatory_body is not None:
                inst.regulatory_body = regulatory_body
            if affiliation_details is not None:
                inst.affiliation_details = affiliation_details
        await session.flush()
        return inst

    @staticmethod
    async def get_by_id(session: AsyncSession, institution_id: str) -> Optional[InstitutionModel]:
        stmt = select(InstitutionModel).where(InstitutionModel.id == institution_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(session: AsyncSession) -> List[InstitutionModel]:
        stmt = select(InstitutionModel)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def list_by_organization(session: AsyncSession, organization_id: str) -> List[InstitutionModel]:
        stmt = select(InstitutionModel).where(
            (InstitutionModel.organization_id == organization_id)
            | (InstitutionModel.parent_organization_id == organization_id)
            | (InstitutionModel.id == organization_id)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def list_constituent_institutions(
        session: AsyncSession,
        organization_id: str,
        user: Optional[UserModel] = None,
    ) -> List[InstitutionModel]:
        """
        Return the constituent institutions belonging to an organization/network.
        When an organization is an educational group/trust/network with child institutions,
        excludes the synthetic parent group container row (id == organization_id) so that
        only actual constituent institutions are counted and analyzed.
        For single-institution entities (e.g., standalone college), returns that institution.
        """
        all_rows = await InstitutionRepository.list_by_organization(session, organization_id)
        children = [r for r in all_rows if r.id != organization_id]
        if children:
            constituents = []
            for r in all_rows:
                if r.id == organization_id and (
                    r.entity_category in (
                        "educational_group_network",
                        "nonprofit_trust_society_foundation",
                        "government_public_body",
                        "private_organization",
                    )
                    or r.entity_type in ("organization", "group", "educational_group_network")
                ):
                    continue
                constituents.append(r)
            if not constituents:
                constituents = children
        else:
            constituents = all_rows

        if user is not None:
            constituents = [i for i in constituents if InstitutionRepository.user_can_access(i, user)]
        return constituents

    @staticmethod
    def user_can_access(inst: InstitutionModel, user: UserModel) -> bool:
        """
        Enforces user isolation, organization isolation, institution permissions,
        and department/program-scoped institution bindings.
        """
        if user.role == "SuperAdmin":
            return True
        if user.id in _USER_ACCESS_POLICIES:
            policy = _USER_ACCESS_POLICIES[user.id]
            if policy.scope_level == AccessScopeLevel.USER:
                return bool(inst.owner_user_id and inst.owner_user_id == user.id)
            if policy.scope_level in (AccessScopeLevel.INSTITUTION, AccessScopeLevel.DEPARTMENT_PROGRAM):
                return inst.id in policy.allowed_institution_ids
            if policy.scope_level == AccessScopeLevel.ORGANIZATION:
                return bool(
                    policy.organization_id
                    and (
                        inst.id == policy.organization_id
                        or inst.organization_id == policy.organization_id
                        or inst.parent_organization_id == policy.organization_id
                    )
                )
        if inst.owner_user_id is None and inst.organization_id is None and inst.parent_organization_id is None:
            return True
        if inst.owner_user_id and inst.owner_user_id == user.id:
            return True
        if user.organization_id and (
            inst.id == user.organization_id
            or inst.organization_id == user.organization_id
            or inst.parent_organization_id == user.organization_id
        ):
            return True
        if user.primary_institution_id and (
            inst.id == user.primary_institution_id or inst.parent_organization_id == user.primary_institution_id
        ):
            return True
        return False

    @staticmethod
    async def list_for_user(session: AsyncSession, user: UserModel) -> List[InstitutionModel]:
        all_insts = await InstitutionRepository.list_all(session)
        return [i for i in all_insts if InstitutionRepository.user_can_access(i, user)]


class HierarchyNodeRepository:
    @staticmethod
    async def upsert(
        session: AsyncSession,
        node_id: str,
        institution_id: str,
        node_type: str,
        code: str,
        name: str,
        organization_id: Optional[str] = None,
        parent_node_id: Optional[str] = None,
        node_type_other: Optional[str] = None,
        academic_domain: Optional[str] = None,
        academic_domain_other: Optional[str] = None,
        degree_or_level: Optional[str] = None,
        sanctioned_intake: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
        owner_user_id: Optional[str] = None,
    ) -> HierarchyNodeModel:
        stmt = select(HierarchyNodeModel).where(HierarchyNodeModel.id == node_id)
        node = (await session.execute(stmt)).scalar_one_or_none()
        meta_json = json.dumps(metadata) if metadata is not None else None
        if not node:
            node = HierarchyNodeModel(
                id=node_id,
                institution_id=institution_id,
                organization_id=organization_id,
                parent_node_id=parent_node_id,
                node_type=node_type,
                node_type_other=node_type_other,
                code=code,
                name=name,
                academic_domain=academic_domain,
                academic_domain_other=academic_domain_other,
                degree_or_level=degree_or_level,
                sanctioned_intake=sanctioned_intake,
                metadata_json=meta_json,
                owner_user_id=owner_user_id,
            )
            session.add(node)
        else:
            node.node_type = node_type
            node.node_type_other = node_type_other
            node.code = code
            node.name = name
            node.parent_node_id = parent_node_id
            node.academic_domain = academic_domain
            node.academic_domain_other = academic_domain_other
            node.degree_or_level = degree_or_level
            node.sanctioned_intake = sanctioned_intake
            if meta_json is not None:
                node.metadata_json = meta_json
        await session.flush()
        return node

    @staticmethod
    async def get_by_id(session: AsyncSession, node_id: str) -> Optional[HierarchyNodeModel]:
        stmt = select(HierarchyNodeModel).where(HierarchyNodeModel.id == node_id)
        return (await session.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def list_by_institution(
        session: AsyncSession,
        institution_id: str,
        node_type: Optional[str] = None,
    ) -> List[HierarchyNodeModel]:
        stmt = select(HierarchyNodeModel).where(HierarchyNodeModel.institution_id == institution_id)
        if node_type:
            stmt = stmt.where(HierarchyNodeModel.node_type == node_type)
        stmt = stmt.order_by(HierarchyNodeModel.node_type, HierarchyNodeModel.code)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def delete_by_id(session: AsyncSession, node_id: str) -> bool:
        node = await HierarchyNodeRepository.get_by_id(session, node_id)
        if not node:
            return False
        await session.delete(node)
        await session.flush()
        return True


class SignalSnapshotRepository:
    @staticmethod
    async def save_signals(
        session: AsyncSession,
        signals: List[AdmissionsSignal | PlacementsSignal | CETRankingSignal]
    ) -> int:
        count = 0
        for s in signals:
            sig_type = "unknown"
            if isinstance(s, AdmissionsSignal):
                sig_type = "admissions"
                year = s.academic_year
            elif isinstance(s, PlacementsSignal):
                sig_type = "placements"
                year = s.graduation_year
            elif isinstance(s, CETRankingSignal):
                sig_type = "cet_ranking"
                year = s.academic_year
            else:
                continue

            prov_id = s.provenance.source_id if s.provenance else "UNKNOWN"
            stmt = select(SignalSnapshotModel).where(
                SignalSnapshotModel.institution_id == s.institution_id,
                SignalSnapshotModel.academic_year == year,
                SignalSnapshotModel.department == s.department,
                SignalSnapshotModel.signal_type == sig_type,
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if existing:
                existing.payload_json = s.model_dump_json()
                existing.provenance_id = prov_id
            else:
                snapshot = SignalSnapshotModel(
                    institution_id=s.institution_id,
                    academic_year=year,
                    department=s.department,
                    signal_type=sig_type,
                    payload_json=s.model_dump_json(),
                    provenance_id=prov_id
                )
                session.add(snapshot)
            count += 1
        await session.flush()
        return count

    @staticmethod
    async def get_by_institution(
        session: AsyncSession,
        institution_id: str,
        signal_type: Optional[str] = None
    ) -> List[SignalSnapshotModel]:
        stmt = select(SignalSnapshotModel).where(SignalSnapshotModel.institution_id == institution_id)
        if signal_type:
            stmt = stmt.where(SignalSnapshotModel.signal_type == signal_type)
        stmt = stmt.order_by(SignalSnapshotModel.academic_year)
        result = await session.execute(stmt)
        return list(result.scalars().all())


class AssessmentRepository:
    @staticmethod
    async def get_by_dataset_version(
        session: AsyncSession,
        institution_id: str,
        dataset_version: str,
    ) -> Optional[CrisisAssessmentModel]:
        stmt = (
            select(CrisisAssessmentModel)
            .where(
                CrisisAssessmentModel.institution_id == institution_id,
                CrisisAssessmentModel.dataset_version == dataset_version,
            )
            .order_by(desc(CrisisAssessmentModel.assessment_timestamp))
            .limit(1)
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def save_assessment(
        session: AsyncSession,
        assessment: CrisisAssessment,
        dataset_version: Optional[str] = None,
        signals_hash: Optional[str] = None,
    ) -> CrisisAssessmentModel:
        if dataset_version:
            existing = await AssessmentRepository.get_by_dataset_version(session, assessment.institution_id, dataset_version)
            if existing:
                existing.cri_score = assessment.composite_risk_index
                existing.risk_level = assessment.risk_level
                existing.primary_threat = assessment.primary_driving_signal
                existing.assessment_json = assessment.model_dump_json()
                existing.assessment_timestamp = assessment.assessment_timestamp
                existing.signals_hash = signals_hash
                await session.flush()
                return existing

        model = CrisisAssessmentModel(
            institution_id=assessment.institution_id,
            dataset_version=dataset_version,
            signals_hash=signals_hash,
            assessment_timestamp=assessment.assessment_timestamp,
            cri_score=assessment.composite_risk_index,
            risk_level=assessment.risk_level,
            primary_threat=assessment.primary_driving_signal,
            assessment_json=assessment.model_dump_json()
        )
        session.add(model)
        await session.flush()
        return model

    @staticmethod
    async def delete_by_institution(session: AsyncSession, institution_id: str) -> int:
        stmt = delete(CrisisAssessmentModel).where(CrisisAssessmentModel.institution_id == institution_id)
        res = await session.execute(stmt)
        await session.flush()
        return res.rowcount or 0

    @staticmethod
    async def get_latest(
        session: AsyncSession,
        institution_id: str
    ) -> Optional[CrisisAssessmentModel]:
        stmt = (
            select(CrisisAssessmentModel)
            .where(CrisisAssessmentModel.institution_id == institution_id)
            .order_by(desc(CrisisAssessmentModel.assessment_timestamp))
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_by_institution(
        session: AsyncSession,
        institution_id: str
    ) -> List[CrisisAssessmentModel]:
        stmt = (
            select(CrisisAssessmentModel)
            .where(CrisisAssessmentModel.institution_id == institution_id)
            .order_by(desc(CrisisAssessmentModel.assessment_timestamp))
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())


class UserRepository:
    @staticmethod
    async def get_by_email(session: AsyncSession, email: str) -> Optional[UserModel]:
        stmt = select(UserModel).where(UserModel.email == email)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_google_sub(session: AsyncSession, google_sub: str) -> Optional[UserModel]:
        stmt = select(UserModel).where(UserModel.google_sub == google_sub)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_id(session: AsyncSession, user_id: str) -> Optional[UserModel]:
        stmt = select(UserModel).where(UserModel.id == user_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def create_user(
        session: AsyncSession,
        user_id: str,
        email: str,
        hashed_password: str,
        role: str = "Viewer",
        auth_provider: str = "local",
        google_sub: Optional[str] = None,
        full_name: Optional[str] = None,
        job_title: Optional[str] = None,
        phone: Optional[str] = None,
        department_or_unit: Optional[str] = None,
        organization_id: Optional[str] = None,
        primary_institution_id: Optional[str] = None,
        onboarding_completed: bool = False,
    ) -> UserModel:
        user = UserModel(
            id=user_id,
            email=email,
            hashed_password=hashed_password,
            role=role,
            is_active=True,
            auth_provider=auth_provider,
            google_sub=google_sub,
            full_name=full_name,
            job_title=job_title,
            phone=phone,
            department_or_unit=department_or_unit,
            organization_id=organization_id,
            primary_institution_id=primary_institution_id,
            onboarding_completed=onboarding_completed,
        )
        session.add(user)
        await session.flush()
        return user

    @staticmethod
    async def update_profile(
        session: AsyncSession,
        user: UserModel,
        full_name: Optional[str] = None,
        job_title: Optional[str] = None,
        phone: Optional[str] = None,
        department_or_unit: Optional[str] = None,
        primary_institution_id: Optional[str] = None,
    ) -> UserModel:
        if full_name is not None:
            user.full_name = full_name
        if job_title is not None:
            user.job_title = job_title
        if phone is not None:
            user.phone = phone
        if department_or_unit is not None:
            user.department_or_unit = department_or_unit
        if primary_institution_id is not None:
            user.primary_institution_id = primary_institution_id
        await session.flush()
        return user

    @staticmethod
    async def update_password(
        session: AsyncSession,
        user: UserModel,
        new_hashed_password: str,
    ) -> UserModel:
        user.hashed_password = new_hashed_password
        if user.auth_provider == "google":
            user.auth_provider = "local+google"
        await session.flush()
        return user

    @staticmethod
    async def authenticate_or_create_google_user(
        session: AsyncSession,
        google_sub: str,
        email: str,
        full_name: Optional[str] = None,
        default_role: str = "Auditor",
    ) -> tuple[UserModel, bool]:
        """
        Authenticate or provision a CIP user from a verified Google OAuth identity.
        - Never duplicates an existing user with the same google_sub or email.
        - Raises ValueError('duplicate_account_conflict: ...') if a conflicting identity binding exists.
        - Returns (user, is_new_user).
        """
        by_sub = await UserRepository.get_by_google_sub(session, google_sub)
        by_email = await UserRepository.get_by_email(session, email)

        if by_sub and by_email and by_sub.id != by_email.id:
            raise ValueError(
                "duplicate_account_conflict: Google account subject is already linked to a different CIP email account."
            )

        if by_sub:
            if full_name and not by_sub.full_name:
                by_sub.full_name = full_name
            await session.flush()
            return by_sub, False

        if by_email:
            if by_email.google_sub and by_email.google_sub != google_sub:
                raise ValueError(
                    "duplicate_account_conflict: An account with this email is already linked to a different Google identity."
                )
            by_email.google_sub = google_sub
            if by_email.auth_provider == "local":
                by_email.auth_provider = "local+google"
            else:
                by_email.auth_provider = "google"
            if full_name and not by_email.full_name:
                by_email.full_name = full_name
            await session.flush()
            return by_email, False

        base_id = f"usr_google_{google_sub[:24]}"
        existing_id = await UserRepository.get_by_id(session, base_id)
        if existing_id:
            base_id = f"usr_google_{google_sub[:16]}_{ abs(hash(email)) % 10000 }"

        user = await UserRepository.create_user(
            session=session,
            user_id=base_id,
            email=email,
            hashed_password="!GOOGLE_OAUTH_USER_NO_LOCAL_PASSWORD!",
            role=default_role,
            auth_provider="google",
            google_sub=google_sub,
            full_name=full_name,
            onboarding_completed=False,
        )
        return user, True

    @staticmethod
    async def complete_onboarding(
        session: AsyncSession,
        user: UserModel,
        institution_id: str,
        organization_id: Optional[str] = None,
    ) -> UserModel:
        user.primary_institution_id = institution_id
        user.organization_id = organization_id or institution_id
        user.onboarding_completed = True
        await session.flush()
        return user


class OTPRepository:
    """
    Dedicated repository for real backend OTP generation, lookup, invalidation,
    and verification. Backed by the otp_verifications database table.
    """

    @staticmethod
    async def create_otp(
        session: AsyncSession,
        email: str,
        purpose: str,
        otp_hash: str,
        expires_at: datetime,
        user_id: Optional[str] = None,
    ) -> OTPVerificationModel:
        """
        Invalidates any previous active OTPs for this email and purpose,
        then persists a new OTP verification record.
        """
        now = datetime.now(timezone.utc)
        # Invalidate previous unused OTPs for the same email & purpose
        await OTPRepository.invalidate_all_for_email(session, email=email, purpose=purpose)

        otp_id = f"otp_{uuid.uuid4().hex[:20]}"
        record = OTPVerificationModel(
            id=otp_id,
            user_id=user_id,
            email=email.strip().lower(),
            purpose=purpose,
            otp_hash=otp_hash,
            attempts=0,
            created_at=now,
            expires_at=expires_at,
            used_at=None,
            invalidated_at=None,
        )
        session.add(record)
        await session.flush()
        return record

    @staticmethod
    async def get_active_otp(
        session: AsyncSession,
        email: str,
        purpose: str,
    ) -> Optional[OTPVerificationModel]:
        """
        Retrieves the latest active (unused, uninvalidated) OTP for the given email and purpose.
        """
        stmt = (
            select(OTPVerificationModel)
            .where(
                OTPVerificationModel.email == email.strip().lower(),
                OTPVerificationModel.purpose == purpose,
                OTPVerificationModel.used_at.is_(None),
                OTPVerificationModel.invalidated_at.is_(None),
            )
            .order_by(desc(OTPVerificationModel.created_at))
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_latest_otp(
        session: AsyncSession,
        email: str,
        purpose: str,
    ) -> Optional[OTPVerificationModel]:
        """
        Retrieves the most recently created OTP record (used to check resend cooldown).
        """
        stmt = (
            select(OTPVerificationModel)
            .where(
                OTPVerificationModel.email == email.strip().lower(),
                OTPVerificationModel.purpose == purpose,
            )
            .order_by(desc(OTPVerificationModel.created_at))
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def increment_attempts(
        session: AsyncSession,
        otp: OTPVerificationModel,
    ) -> int:
        """
        Increments the failed attempt counter for an OTP record.
        """
        otp.attempts += 1
        await session.flush()
        return otp.attempts

    @staticmethod
    async def mark_used(
        session: AsyncSession,
        otp: OTPVerificationModel,
    ) -> None:
        """
        Marks an OTP record as successfully used and invalidates it for further use.
        """
        otp.used_at = datetime.now(timezone.utc)
        await session.flush()

    @staticmethod
    async def invalidate_otp(
        session: AsyncSession,
        otp: OTPVerificationModel,
    ) -> None:
        """
        Invalidates a specific OTP record (e.g. on expiration, too many attempts, or resend).
        """
        otp.invalidated_at = datetime.now(timezone.utc)
        await session.flush()

    @staticmethod
    async def invalidate_all_for_email(
        session: AsyncSession,
        email: str,
        purpose: Optional[str] = None,
    ) -> int:
        """
        Invalidates all unused OTP records for an email (and optional purpose).
        """
        stmt = select(OTPVerificationModel).where(
            OTPVerificationModel.email == email.strip().lower(),
            OTPVerificationModel.used_at.is_(None),
            OTPVerificationModel.invalidated_at.is_(None),
        )
        if purpose:
            stmt = stmt.where(OTPVerificationModel.purpose == purpose)

        result = await session.execute(stmt)
        records = result.scalars().all()
        now = datetime.now(timezone.utc)
        for r in records:
            r.invalidated_at = now
        if records:
            await session.flush()
        return len(records)


class RevokedTokenRepository:
    """
    Persisted revoked JWT token registry for immediate server-side logout enforcement.
    Backed by the revoked_tokens database table.
    """

    @staticmethod
    async def revoke_token(
        session: AsyncSession,
        token: str,
        expires_at: Optional[datetime] = None,
    ) -> RevokedTokenModel:
        import hashlib
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        stmt = select(RevokedTokenModel).where(RevokedTokenModel.id == token_hash)
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing:
            return existing

        rec = RevokedTokenModel(
            id=token_hash,
            token=token[-32:],
            revoked_at=datetime.now(timezone.utc),
            expires_at=expires_at,
        )
        session.add(rec)
        await session.flush()
        return rec

    @staticmethod
    async def is_token_revoked(
        session: AsyncSession,
        token: str,
    ) -> bool:
        import hashlib
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        stmt = select(RevokedTokenModel).where(RevokedTokenModel.id == token_hash)
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None


class DiscoveredSignalRepository:
    @staticmethod
    async def save_discovered_signals(
        session: AsyncSession,
        ingestion_id: str,
        signals: List[DiscoveredSignal],
        existing_updated: Optional[List[DiscoveredSignal]] = None,
    ) -> int:
        """
        Persist newly discovered signals without overwriting previous conflicting signals.
        Also updates any pre-existing signal rows that were linked into a contradiction group.
        """
        if existing_updated:
            for prev_sig in existing_updated:
                if prev_sig.is_contradictory:
                    stmt = select(DiscoveredSignalModel).where(DiscoveredSignalModel.id == prev_sig.signal_id)
                    row = (await session.execute(stmt)).scalar_one_or_none()
                    if row:
                        row.is_contradictory = True
                        row.contradiction_group_id = prev_sig.contradiction_group_id
                        row.payload_json = prev_sig.model_dump_json()

        # Step 1: Use all signals (including flagged duplicates for provenance and audit)
        valid_signals = list(signals)
        if not valid_signals:
            return 0

        # Step 2: Check existing signal IDs and fingerprints in DB for the institution to prevent duplicate inserts
        import hashlib
        inst_id = valid_signals[0].context.institution_id
        existing_ids = set()
        existing_fps = set()
        if inst_id:
            all_ids = [s.signal_id for s in valid_signals]
            for i in range(0, len(all_ids), 500):
                chunk = all_ids[i : i + 500]
                check_stmt = select(DiscoveredSignalModel.id).where(
                    DiscoveredSignalModel.institution_id == inst_id,
                    DiscoveredSignalModel.id.in_(chunk)
                )
                res = await session.execute(check_stmt)
                existing_ids.update(res.scalars().all())

            all_fps = []
            for s in valid_signals:
                fp_val = getattr(s, "fingerprint", None)
                if not fp_val:
                    parts = [
                        str(s.context.institution_id or "").strip().lower(),
                        str(s.domain or "").strip().lower(),
                        str(s.metric_name or "").strip().lower(),
                        str(s.context.academic_year or s.context.time_period or "").strip().lower(),
                        str(s.context.department or "").strip().lower(),
                        str(s.provenance.spreadsheet_location or s.provenance.page_or_section or "").strip().lower(),
                        str(s.raw_value or "").strip().lower(),
                    ]
                    fp_val = hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()[:24]
                    s.fingerprint = fp_val
                all_fps.append(fp_val)

            for i in range(0, len(all_fps), 500):
                chunk_fps = all_fps[i : i + 500]
                check_fp_stmt = select(DiscoveredSignalModel.fingerprint).where(
                    DiscoveredSignalModel.institution_id == inst_id,
                    DiscoveredSignalModel.fingerprint.in_(chunk_fps)
                )
                res_fp = await session.execute(check_fp_stmt)
                existing_fps.update(res_fp.scalars().all())

        count = 0
        seen_in_batch = set()
        seen_fps_in_batch = set()
        for s in valid_signals:
            fp = s.fingerprint
            if s.signal_id in existing_ids or (fp and fp in existing_fps) or s.signal_id in seen_in_batch or (fp and fp in seen_fps_in_batch):
                continue
            seen_in_batch.add(s.signal_id)
            if fp:
                seen_fps_in_batch.add(fp)

            model = DiscoveredSignalModel(
                id=s.signal_id,
                ingestion_id=ingestion_id,
                organization_id=s.context.organization_id,
                institution_id=s.context.institution_id,
                department=s.context.department,
                program=s.context.program,
                time_period=s.context.time_period,
                academic_year=s.context.academic_year,
                domain=s.domain,
                metric_name=s.metric_name,
                metric_label=s.metric_label,
                value=s.value,
                raw_value=s.raw_value,
                unit=s.unit,
                polarity=s.polarity,
                risk_contribution=s.risk_contribution,
                is_contradictory=s.is_contradictory,
                contradiction_group_id=s.contradiction_group_id,
                is_duplicate=s.is_duplicate,
                source_document=s.provenance.document,
                format_type=s.provenance.format_type,
                page_or_section=s.provenance.page_or_section,
                spreadsheet_location=s.provenance.spreadsheet_location,
                excerpt_or_reference=s.provenance.excerpt_or_reference,
                extraction_confidence=s.provenance.extraction_confidence,
                payload_json=s.model_dump_json(),
                fingerprint=fp,
            )
            session.add(model)
            count += 1
            if count % 500 == 0:
                await session.flush()
        await session.flush()
        return count

    @staticmethod
    async def delete_by_ingestion_id(session: AsyncSession, ingestion_id: str) -> int:
        """Safely delete all discovered signals associated with a specific ingestion dataset."""
        stmt = delete(DiscoveredSignalModel).where(DiscoveredSignalModel.ingestion_id == ingestion_id)
        result = await session.execute(stmt)
        await session.flush()
        return result.rowcount or 0

    @staticmethod
    async def get_by_institution(
        session: AsyncSession,
        institution_id: str,
        domain: Optional[str] = None,
        contradictory_only: bool = False,
    ) -> List[DiscoveredSignal]:
        stmt = select(DiscoveredSignalModel).where(DiscoveredSignalModel.institution_id == institution_id)
        if domain:
            stmt = stmt.where(DiscoveredSignalModel.domain == domain)
        if contradictory_only:
            stmt = stmt.where(DiscoveredSignalModel.is_contradictory == True)  # noqa: E712
        stmt = stmt.order_by(DiscoveredSignalModel.created_at)
        rows = list((await session.execute(stmt)).scalars().all())
        out: List[DiscoveredSignal] = []
        for r in rows:
            try:
                sig = DiscoveredSignal.model_validate_json(r.payload_json)
                sig.is_contradictory = r.is_contradictory
                sig.contradiction_group_id = r.contradiction_group_id
                sig.is_duplicate = r.is_duplicate
                out.append(sig)
            except Exception:
                pass
        return out


class IngestionRecordRepository:
    @staticmethod
    async def get_by_content_hash(
        session: AsyncSession,
        institution_id: str,
        content_hash: str,
    ) -> Optional[IngestionRecordModel]:
        stmt = (
            select(IngestionRecordModel)
            .where(
                IngestionRecordModel.institution_id == institution_id,
                IngestionRecordModel.content_hash == content_hash,
            )
            .order_by(desc(IngestionRecordModel.created_at))
            .limit(1)
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def save_record(
        session: AsyncSession,
        result: UniversalIngestionResult,
        owner_user_id: Optional[str] = None,
        content_hash: Optional[str] = None,
        dataset_version: int = 1,
    ) -> IngestionRecordModel:
        rec = IngestionRecordModel(
            id=result.ingestion_id,
            organization_id=result.organization_id,
            institution_id=result.institution_id,
            owner_user_id=owner_user_id,
            filename=result.filename,
            content_hash=content_hash,
            dataset_version=dataset_version,
            detected_format=result.detected_format,
            mime_type=result.mime_type,
            status=result.status,
            truthful_explanation=result.truthful_explanation,
            total_signals=result.total_signals_ingested,
            contradictions_detected=result.contradictions_detected,
            duplicates_detected=result.duplicates_detected,
            extraction_confidence=result.extraction_confidence,
            result_json=result.model_dump_json(),
        )
        session.add(rec)
        await session.flush()
        return rec

    @staticmethod
    async def list_by_institution(
        session: AsyncSession,
        institution_id: str,
    ) -> List[UniversalIngestionResult]:
        stmt = (
            select(IngestionRecordModel)
            .where(IngestionRecordModel.institution_id == institution_id)
            .order_by(desc(IngestionRecordModel.created_at))
        )
        rows = list((await session.execute(stmt)).scalars().all())
        out: List[UniversalIngestionResult] = []
        for r in rows:
            try:
                out.append(UniversalIngestionResult.model_validate_json(r.result_json))
            except Exception:
                pass
        return out

    @staticmethod
    async def get_by_id(session: AsyncSession, ingestion_id: str) -> Optional[IngestionRecordModel]:
        stmt = select(IngestionRecordModel).where(IngestionRecordModel.id == ingestion_id)
        return (await session.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def delete_by_id(session: AsyncSession, ingestion_id: str) -> bool:
        stmt = delete(IngestionRecordModel).where(IngestionRecordModel.id == ingestion_id)
        res = await session.execute(stmt)
        await session.flush()
        return bool(res.rowcount and res.rowcount > 0)


class InstitutionalMemoryRepository:
    """
    Persists and retrieves CIP Phase 4 longitudinal institutional memory across:
    observed_fact, analysis, inference, prediction, outcome, user_feedback, unknown.
    Never overwrites observed_fact with user_feedback.
    """

    @staticmethod
    async def upsert_entry(
        session: AsyncSession,
        entry: InstitutionalMemoryEntry,
        organization_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> InstitutionalMemoryEntryModel:
        stmt = select(InstitutionalMemoryEntryModel).where(
            InstitutionalMemoryEntryModel.id == entry.memory_id
        )
        row = (await session.execute(stmt)).scalar_one_or_none()
        if not row:
            row = InstitutionalMemoryEntryModel(
                id=entry.memory_id,
                institution_id=entry.institution_id,
                organization_id=organization_id,
                category=entry.category.value if isinstance(entry.category, MemoryCategory) else str(entry.category),
                domain=entry.domain,
                metric_or_topic=entry.metric_or_topic,
                academic_year=entry.academic_year,
                statement=entry.statement,
                confidence=entry.confidence,
                evidence_refs_json=json.dumps(entry.evidence_refs, default=str),
                payload_json=json.dumps(entry.payload, default=str),
                created_by_user_id=user_id,
                created_at=entry.created_at,
            )
            session.add(row)
        else:
            row.statement = entry.statement
            row.confidence = entry.confidence
            row.evidence_refs_json = json.dumps(entry.evidence_refs, default=str)
            row.payload_json = json.dumps(entry.payload, default=str)
        await session.flush()
        return row

    @staticmethod
    async def save_entries_bulk(
        session: AsyncSession,
        entries: List[InstitutionalMemoryEntry],
        organization_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> int:
        count = 0
        for entry in entries:
            await InstitutionalMemoryRepository.upsert_entry(
                session=session,
                entry=entry,
                organization_id=organization_id,
                user_id=user_id,
            )
            count += 1
        return count

    @staticmethod
    async def list_by_institution(
        session: AsyncSession,
        institution_id: str,
        category: Optional[str] = None,
    ) -> List[InstitutionalMemoryEntry]:
        stmt = select(InstitutionalMemoryEntryModel).where(
            InstitutionalMemoryEntryModel.institution_id == institution_id
        )
        if category:
            stmt = stmt.where(InstitutionalMemoryEntryModel.category == category)
        stmt = stmt.order_by(InstitutionalMemoryEntryModel.created_at)
        rows = list((await session.execute(stmt)).scalars().all())
        out: List[InstitutionalMemoryEntry] = []
        for r in rows:
            try:
                refs = json.loads(r.evidence_refs_json) if r.evidence_refs_json else []
            except Exception:
                refs = []
            try:
                payload = json.loads(r.payload_json) if r.payload_json else {}
            except Exception:
                payload = {}
            out.append(
                InstitutionalMemoryEntry(
                    memory_id=r.id,
                    institution_id=r.institution_id,
                    category=MemoryCategory(r.category),
                    domain=r.domain,
                    metric_or_topic=r.metric_or_topic,
                    academic_year=r.academic_year,
                    statement=r.statement,
                    confidence=r.confidence,
                    evidence_refs=refs,
                    payload=payload,
                    created_at=r.created_at,
                )
            )
        return out

    @staticmethod
    async def save_user_feedback(
        session: AsyncSession,
        institution_id: str,
        submission: UserFeedbackSubmission,
        user_id: Optional[str] = None,
        organization_id: Optional[str] = None,
    ) -> UserFeedbackRecord:
        """
        Persist user feedback ('Confirmed', 'Incorrect', 'Insufficient Evidence').
        Strictly stored under category 'user_feedback' and never blindly converted
        into 'observed_fact'.
        """
        import uuid
        from datetime import datetime, timezone

        feedback_id = f"fb_{institution_id}_{uuid.uuid4().hex[:10]}"
        now = datetime.now(timezone.utc)

        record = UserFeedbackRecord(
            feedback_id=feedback_id,
            institution_id=institution_id,
            target_id=submission.target_id,
            target_category=submission.target_category,
            verdict=submission.verdict,
            stored_category="user_feedback",
            converted_to_observed_fact=False,
            domain=submission.domain,
            metric_or_topic=submission.metric_or_topic,
            academic_year=submission.academic_year,
            reviewer_notes=submission.reviewer_notes,
            submitted_by_user_id=user_id,
            created_at=now,
        )

        stmt_text = (
            f"User feedback on {submission.target_category} '{submission.target_id}': "
            f"verdict='{submission.verdict.value}'."
            + (f" Notes: {submission.reviewer_notes}" if submission.reviewer_notes else "")
        )

        entry = InstitutionalMemoryEntry(
            memory_id=feedback_id,
            institution_id=institution_id,
            category=MemoryCategory.USER_FEEDBACK,
            domain=submission.domain,
            metric_or_topic=submission.metric_or_topic,
            academic_year=submission.academic_year,
            statement=stmt_text,
            confidence=1.0,
            evidence_refs=[submission.target_id],
            payload=record.model_dump(mode="json"),
            created_at=now,
        )
        await InstitutionalMemoryRepository.upsert_entry(
            session=session,
            entry=entry,
            organization_id=organization_id,
            user_id=user_id,
        )
        return record



