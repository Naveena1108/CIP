"""
CIP Phase 7 Contracts: Organization / Network Intelligence and Cross-Institution Integration.

Supports:
- Organization / Educational Group -> multiple institutions -> departments/programs -> data/signals
- Heterogeneous institution types (school, PUC, college, engineering, medical, etc.) without hardcoding
- Institution View vs. Organization/Network View
- Organization-level analysis:
  * institution_count
  * data_coverage
  * institution_level_risks
  * common_emerging_patterns (strictly non-causal co-occurrence observations)
  * cross_institution_comparisons (where evidence and comparability permit)
- Granular access control (user, organization, institution, department/program permissions)
- End-to-end organization context propagation across authentication, profiles, data ingestion,
  signals, intelligence, evidence, predictions, memory, and reports.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

from .institutional_intelligence import RiskStage
from .forecasting_memory import ForecastStatus
from .evidence_investigation import ProvenanceRecord, InsightWithEvidence


class AccessScopeLevel(str, Enum):
    """
    Four-tier permission model for CIP Phase 7:
    - USER: Isolated individual workspace access
    - ORGANIZATION: Full organization/network-wide access across all constituent institutions
    - INSTITUTION: Scoped strictly to one or more designated institutions within an organization
    - DEPARTMENT_PROGRAM: Scoped strictly to specific department(s)/program(s) within a designated institution
    """
    USER = "user"
    ORGANIZATION = "organization"
    INSTITUTION = "institution"
    DEPARTMENT_PROGRAM = "department_program"


class UserAccessPermissionPolicy(BaseModel):
    """
    Resolved permission policy for a user across user, organization, institution,
    and department/program scopes.
    """
    user_id: str
    scope_level: AccessScopeLevel = AccessScopeLevel.ORGANIZATION
    access_scope_level: Optional[AccessScopeLevel] = None
    organization_id: Optional[str] = None
    allowed_organization_ids: List[str] = Field(default_factory=list)
    allowed_institution_ids: List[str] = Field(
        default_factory=list,
        description="Empty list when scope_level is ORGANIZATION (all org institutions allowed); explicit list when INSTITUTION or DEPARTMENT_PROGRAM",
    )
    allowed_departments_programs: List[str] = Field(
        default_factory=list,
        description="Explicit list of permitted department/program codes when scope_level is DEPARTMENT_PROGRAM",
    )
    can_view_organization_network: bool = True
    description: str = ""

    def model_post_init(self, __context: Any) -> None:
        if self.access_scope_level is None:
            self.access_scope_level = self.scope_level
        if self.organization_id and not self.allowed_organization_ids:
            self.allowed_organization_ids = [self.organization_id]


class AccessPermissionUpsertRequest(BaseModel):
    """
    Request payload to configure or update a user's access scope (organization, institution, or department/program).
    """
    target_user_id: Optional[str] = Field(
        default=None,
        description="User ID to configure; defaults to the authenticated user if omitted",
    )
    target_email: Optional[str] = Field(
        default=None,
        description="Email address of the target user to configure (alternative to target_user_id)",
    )
    scope_level: Optional[AccessScopeLevel] = None
    access_scope_level: Optional[AccessScopeLevel] = None
    organization_id: Optional[str] = None
    allowed_organization_ids: List[str] = Field(default_factory=list)
    allowed_institution_ids: List[str] = Field(default_factory=list)
    allowed_departments_programs: List[str] = Field(default_factory=list)
    allowed_departments_by_institution: Dict[str, List[str]] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if self.scope_level is None and self.access_scope_level is not None:
            self.scope_level = self.access_scope_level
        if self.scope_level is None:
            self.scope_level = AccessScopeLevel.ORGANIZATION
        self.access_scope_level = self.scope_level
        if not self.organization_id and self.allowed_organization_ids:
            self.organization_id = self.allowed_organization_ids[0]
        if not self.allowed_departments_programs and self.allowed_departments_by_institution:
            flat_depts: List[str] = []
            for d_list in self.allowed_departments_by_institution.values():
                for d in d_list:
                    if d not in flat_depts:
                        flat_depts.append(d)
            self.allowed_departments_programs = flat_depts


class DepartmentProgramSignalSummary(BaseModel):
    """
    Hierarchy node summary for a department, program, stream, or wing inside an institution.
    Organization -> Institution -> Department/Program -> Signals.
    """
    node_code: str
    node_name: str
    code: Optional[str] = None
    name: Optional[str] = None
    node_type: str = "department"
    signal_count: int = 0
    domains_present: List[str] = Field(default_factory=list)
    latest_academic_year: Optional[int] = None
    latest_metrics: Dict[str, float] = Field(default_factory=dict)
    anomaly_count: int = 0

    def model_post_init(self, __context: Any) -> None:
        if not self.code:
            self.code = self.node_code
        if not self.name:
            self.name = self.node_name


class ConstituentInstitutionSummary(BaseModel):
    """
    Summary of a single constituent institution within an Organization / Educational Group.
    Supports heterogeneous institution types: school, PUC, degree college, engineering, medical, etc.
    """
    institution_id: str
    name: str
    short_name: Optional[str] = None
    organization_id: Optional[str] = None
    entity_category: str = "education_institution"
    education_entity_type: Optional[str] = Field(
        default=None,
        description="Institution type, e.g., high_school, pre_university_pu_junior_college, degree_college, engineering_technical_institution, medical_health_sciences_institution",
    )
    institution_type: Optional[str] = Field(
        default=None,
        description="Normalized institution type slug (e.g., school, puc, college, engineering, medical, university)",
    )
    institution_type_label: str = Field(
        default="Educational Institution",
        description="Human-readable institution type label (e.g., High School, PUC / Junior College, Engineering College, Medical College)",
    )
    academic_domains: List[str] = Field(default_factory=list)
    has_data: bool = False
    signal_count: int = 0
    canonical_signal_count: int = 0
    dynamic_signal_count: int = 0
    distinct_periods: int = 0
    years_covered: List[int] = Field(default_factory=list)
    domains_covered: List[str] = Field(default_factory=list)
    departments_programs: List[DepartmentProgramSignalSummary] = Field(default_factory=list)
    departments_and_programs: List[DepartmentProgramSignalSummary] = Field(default_factory=list)
    composite_risk_index: Optional[float] = Field(
        default=None,
        description="Institution-specific CRI (0.0-1.0) when data exists; None when no data is ingested",
    )
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL", "INSUFFICIENT_DATA"] = "INSUFFICIENT_DATA"
    risk_progression_stage: str = Field(
        default="insufficient_evidence",
        description="5-stage progression: observation, anomaly, emerging_risk, institutional_risk, crisis, or insufficient_evidence",
    )
    risk_stage: str = Field(
        default="Observation",
        description="Human-readable 5-stage risk label (Observation, Anomaly, Emerging Risk, Institutional Risk, Crisis)",
    )
    primary_driving_signal: str = "Insufficient Data"
    anomaly_count: int = 0
    total_anomalies: int = 0
    forecast_status: str = "INSUFFICIENT_EVIDENCE"
    projected_year3_cri: Optional[float] = None
    provenance_record_count: int = 0
    memory_entry_count: int = 0

    def model_post_init(self, __context: Any) -> None:
        if self.departments_programs and not self.departments_and_programs:
            self.departments_and_programs = list(self.departments_programs)
        elif self.departments_and_programs and not self.departments_programs:
            self.departments_programs = list(self.departments_and_programs)
        if self.total_anomalies == 0 and self.anomaly_count > 0:
            self.total_anomalies = self.anomaly_count
        elif self.anomaly_count == 0 and self.total_anomalies > 0:
            self.anomaly_count = self.total_anomalies
        stage_map = {
            "insufficient_evidence": "Observation",
            "observation": "Observation",
            "anomaly": "Anomaly",
            "emerging_risk": "Emerging Risk",
            "institutional_risk": "Institutional Risk",
            "crisis": "Crisis",
        }
        raw_s = (self.risk_progression_stage or "").strip().lower()
        if raw_s in stage_map:
            self.risk_stage = stage_map[raw_s]


class OrganizationInstitutionCountBreakdown(BaseModel):
    """
    Detailed breakdown of constituent institutions in the organization/network.
    """
    total_institutions: int = 0
    institutions_with_data: int = 0
    institutions_without_data: int = 0
    with_ingested_data: int = 0
    without_data: int = 0
    by_education_type: Dict[str, int] = Field(default_factory=dict)
    by_type: Dict[str, int] = Field(default_factory=dict)
    by_risk_level: Dict[str, int] = Field(default_factory=dict)
    by_risk_stage: Dict[str, int] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if self.with_ingested_data == 0 and self.institutions_with_data > 0:
            self.with_ingested_data = self.institutions_with_data
        if self.without_data == 0 and self.institutions_without_data > 0:
            self.without_data = self.institutions_without_data
        if not self.by_type and self.by_education_type:
            self.by_type = dict(self.by_education_type)


class OrganizationCoverageSummary(BaseModel):
    """
    Organization-wide data coverage across constituent institutions, departments/programs, and signal domains.
    """
    total_institutions: int = 0
    institutions_with_data: int = 0
    institutions_without_data: int = 0
    institutions_with_any_data: int = 0
    institutions_with_multi_year_history: int = 0
    institution_coverage_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    overall_coverage_pct: float = 0.0
    institution_types_represented: List[str] = Field(default_factory=list)
    total_departments_programs: int = 0
    total_signals_ingested: int = 0
    domains_covered_across_network: List[str] = Field(default_factory=list)
    domains_covered_across_org: List[str] = Field(default_factory=list)
    years_covered_across_network: List[int] = Field(default_factory=list)
    per_institution_coverage: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    coverage_gaps: List[str] = Field(default_factory=list)

    def model_post_init(self, __context: Any) -> None:
        if self.institutions_with_any_data == 0 and self.institutions_with_data > 0:
            self.institutions_with_any_data = self.institutions_with_data
        if self.overall_coverage_pct == 0.0 and self.institution_coverage_ratio > 0.0:
            self.overall_coverage_pct = round(self.institution_coverage_ratio * 100.0, 2)
        if not self.domains_covered_across_org and self.domains_covered_across_network:
            self.domains_covered_across_org = list(self.domains_covered_across_network)


class CrossInstitutionPattern(BaseModel):
    """
    Common or emerging pattern observed across multiple institutions in an organization/network.

    NON-CAUSAL EPISTEMIC REQUIREMENT:
    Do not infer a shared cause merely because multiple institutions show similar changes.
    Every pattern explicitly separates observed co-occurrence from causal attribution.
    """
    pattern_id: str
    domain: str
    metric_name: str
    pattern_type: Literal[
        "CO_OCCURRING_ANOMALY",
        "DIRECTIONAL_CO_MOVEMENT",
        "SHARED_DOMAIN_PRESSURE",
        "DIVERGENT_INSTITUTIONAL_TRAJECTORY",
    ]
    direction: Literal["WORSENING", "IMPROVING", "MIXED", "ELEVATED_RISK"]
    institutions_involved: List[str] = Field(default_factory=list)
    institution_names_involved: List[str] = Field(default_factory=list)
    institution_types_involved: List[str] = Field(default_factory=list)
    academic_years: List[int] = Field(default_factory=list)
    epistemic_classification: Literal["OBSERVED_CO_OCCURRENCE"] = "OBSERVED_CO_OCCURRENCE"
    shared_cause_inferred: Literal[False] = False
    non_causal_explanation: str = Field(
        default=(
            "OBSERVED CO-OCCURRENCE ONLY: Similar or simultaneous changes across multiple institutions "
            "do not establish a shared root cause. Each institution operates within distinct academic, "
            "demographic, and local conditions unless direct shared-governance or resource-transfer provenance is verified."
        )
    )
    potential_independent_factors: List[str] = Field(default_factory=list)
    potential_organization_factors_to_investigate: List[str] = Field(default_factory=list)
    summary: str
    supporting_evidence: List[ProvenanceRecord] = Field(default_factory=list)


class CrossInstitutionComparisonEntry(BaseModel):
    """Single institution entry inside a cross-institution comparison table."""
    institution_id: str
    institution_name: str
    education_entity_type: Optional[str] = None
    institution_type_label: str
    academic_year: Optional[int] = None
    observed_value: float
    baseline_value: Optional[float] = None
    unit_or_format: str = "numeric"
    risk_stage: Optional[str] = None
    evidence_ref: Optional[str] = None


class CrossInstitutionComparison(BaseModel):
    """
    Evidence-grounded cross-institution comparison where comparable metrics and evidence permit.
    Explicitly records which institutions are included vs. excluded (due to non-comparable
    institution type or missing metric coverage).
    """
    comparison_id: str
    domain: str
    metric_name: str
    metric_key: Optional[str] = None
    metric_label: str
    comparability_basis: str = Field(
        ...,
        description="Explanation of why these institutions/metrics can be compared and any structural caveats across institution types",
    )
    comparable_evidence_permits: bool = True
    comparable_institutions_count: int = 0
    entries: List[CrossInstitutionComparisonEntry] = Field(default_factory=list)
    excluded_institutions: List[Dict[str, str]] = Field(
        default_factory=list,
        description="Institutions excluded from this comparison with explicit reason (e.g. non-comparable institution type or metric not reported)",
    )
    institutions_excluded: List[str] = Field(default_factory=list)
    exclusion_reasons: List[str] = Field(default_factory=list)
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    mean_value: Optional[float] = None
    spread: Optional[float] = None
    comparison_summary: str
    non_causal_note: str = (
        "Cross-institution metric comparison reflects observed institutional differences and does not imply uniform benchmarks across distinct institution types."
    )

    def model_post_init(self, __context: Any) -> None:
        if not self.metric_key:
            self.metric_key = self.metric_name
        self.comparable_evidence_permits = len(self.entries) >= 2
        if self.excluded_institutions and not self.institutions_excluded:
            self.institutions_excluded = [
                str(ex.get("institution_id", ""))
                for ex in self.excluded_institutions
                if ex.get("institution_id")
            ]
        if self.excluded_institutions and not self.exclusion_reasons:
            self.exclusion_reasons = [
                f"{ex.get('institution_id', '')}: {ex.get('reason', '')}"
                for ex in self.excluded_institutions
            ]


class OrganizationNetworkIntelligenceReport(BaseModel):
    """
    Top-level CIP Phase 7 Organization / Network Intelligence Report.
    Provides:
    - view_type ('ORGANIZATION_NETWORK_VIEW') / view_mode ('organization_network_view')
    - institution_count (with full breakdown)
    - data_coverage
    - institution_level_risks
    - common_emerging_patterns (strictly non-causal)
    - cross_institution_comparisons (where evidence permits)
    - integrated context for signals, evidence, predictions, memory, and reports
    """
    organization_id: str
    organization_name: str
    short_name: Optional[str] = None
    entity_category: str = "educational_group_network"
    ownership_governance: Optional[str] = None
    view_type: Literal["ORGANIZATION_NETWORK_VIEW"] = "ORGANIZATION_NETWORK_VIEW"
    view_mode: str = "organization_network_view"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    access_scope_applied: UserAccessPermissionPolicy
    institution_count: int
    institution_count_breakdown: OrganizationInstitutionCountBreakdown
    institution_breakdown: Optional[OrganizationInstitutionCountBreakdown] = None
    data_coverage: OrganizationCoverageSummary
    institution_level_risks: List[ConstituentInstitutionSummary] = Field(default_factory=list)
    overall_network_risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL", "INSUFFICIENT_DATA"] = "INSUFFICIENT_DATA"
    mean_composite_risk_index: Optional[float] = None
    max_composite_risk_index: Optional[float] = None
    highest_risk_institution_id: Optional[str] = None
    common_emerging_patterns: List[CrossInstitutionPattern] = Field(default_factory=list)
    cross_institution_comparisons: List[CrossInstitutionComparison] = Field(default_factory=list)
    network_evidence_summary: Dict[str, Any] = Field(default_factory=dict)
    network_predictions_summary: Dict[str, Any] = Field(default_factory=dict)
    network_memory_summary: Dict[str, Any] = Field(default_factory=dict)
    executive_summary: str
    causal_guardrail_notice: str = (
        "EPISTEMIC GUARDRAIL: Organization/network analysis identifies co-occurring anomalies and "
        "cross-institution patterns from observed institutional data. Co-occurrence across institutions "
        "never implies a shared cause without direct causal provenance."
    )

    def model_post_init(self, __context: Any) -> None:
        if self.institution_breakdown is None:
            self.institution_breakdown = self.institution_count_breakdown
