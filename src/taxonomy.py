"""
CIP Phase 1 Progressive Entity Classification Taxonomy & Applicable Profile Field Rules.
Supports progressive classification across:
  A. Entity Category
  B. Ownership / Governance
  C. Education / Entity Type (including University Types & Organization/Group Structures)
  D. Academic Domains (single or multi-select)
  E. Mandatory Custom "Other" ("Please specify") validation
"""

from typing import Any, Dict, List, Optional


ENTITY_CATEGORIES: List[Dict[str, str]] = [
    {"id": "educational_institution", "label": "Educational Institution"},
    {"id": "educational_group_network", "label": "Educational Group/Network"},
    {"id": "government_public_body", "label": "Government/Public Body"},
    {"id": "private_organization", "label": "Private Organization"},
    {"id": "nonprofit_trust_society_foundation", "label": "Nonprofit/Trust/Society/Foundation"},
    {"id": "research_academic_body", "label": "Research/Academic Body"},
    {"id": "other", "label": "Other"},
]

# Applicable Ownership / Governance options per Entity Category (avoids forcing irrelevant options)
GOVERNANCE_BY_CATEGORY: Dict[str, List[str]] = {
    "educational_institution": [
        "Central Government",
        "State Government",
        "Local Government",
        "Public/Autonomous",
        "Private",
        "Trust",
        "Society",
        "Foundation",
        "Corporate",
        "Other",
    ],
    "educational_group_network": [
        "Trust",
        "Society",
        "Foundation",
        "Private",
        "Corporate",
        "Public/Autonomous",
        "State Government",
        "Central Government",
        "Other",
    ],
    "government_public_body": [
        "Central Government",
        "State Government",
        "Local Government",
        "Public/Autonomous",
        "Other",
    ],
    "private_organization": [
        "Private",
        "Corporate",
        "Trust",
        "Society",
        "Foundation",
        "Other",
    ],
    "nonprofit_trust_society_foundation": [
        "Trust",
        "Society",
        "Foundation",
        "Public/Autonomous",
        "Other",
    ],
    "research_academic_body": [
        "Central Government",
        "State Government",
        "Public/Autonomous",
        "Private",
        "Trust",
        "Society",
        "Foundation",
        "Corporate",
        "Other",
    ],
    "other": [
        "Central Government",
        "State Government",
        "Local Government",
        "Public/Autonomous",
        "Private",
        "Trust",
        "Society",
        "Foundation",
        "Corporate",
        "Other",
    ],
}

EDUCATION_ENTITY_TYPES_INSTITUTION: List[str] = [
    "Preschool/Early Childhood",
    "Primary School",
    "Middle/Upper Primary",
    "High/Secondary School",
    "Higher/Senior Secondary",
    "PUC/Pre-University",
    "Vocational/Technical School",
    "College",
    "University",
    "Open/Distance Learning",
    "Training/Skill Institution",
    "Research Institution",
    "Other",
]

ORGANIZATION_GROUP_STRUCTURES: List[str] = [
    "School Group",
    "College Group",
    "University System",
    "Educational Trust",
    "Educational Society/Sangha",
    "Education Network",
    "Other",
]

GOVERNMENT_ENTITY_TYPES: List[str] = [
    "Higher Education Department / Directorate",
    "School Education Board / Directorate",
    "Technical Education Board / Council",
    "University System",
    "Public Examination / Regulatory Authority",
    "Municipal / Local Education Body",
    "Other",
]

RESEARCH_ENTITY_TYPES: List[str] = [
    "Research Institution",
    "National / State Research Laboratory",
    "Inter-University Research Centre",
    "Policy & Academic Think Tank",
    "Training/Skill Institution",
    "Other",
]

ENTITY_TYPES_BY_CATEGORY: Dict[str, List[str]] = {
    "educational_institution": EDUCATION_ENTITY_TYPES_INSTITUTION,
    "educational_group_network": ORGANIZATION_GROUP_STRUCTURES,
    "nonprofit_trust_society_foundation": ORGANIZATION_GROUP_STRUCTURES,
    "private_organization": ORGANIZATION_GROUP_STRUCTURES + ["Training/Skill Institution", "College", "University"],
    "government_public_body": GOVERNMENT_ENTITY_TYPES,
    "research_academic_body": RESEARCH_ENTITY_TYPES,
    "other": EDUCATION_ENTITY_TYPES_INSTITUTION + ORGANIZATION_GROUP_STRUCTURES,
}

UNIVERSITY_TYPES: List[str] = [
    "Central University",
    "State Public University",
    "State Private University",
    "Deemed-to-be University",
    "Institute of National Importance / Autonomous",
    "Affiliating Technical University",
    "Open / Distance University",
    "Research University",
    "Other",
]

ACADEMIC_DOMAINS: List[str] = [
    "Engineering & Technology",
    "Medicine",
    "Dentistry",
    "Pharmacy",
    "Nursing",
    "Allied Health",
    "Agriculture",
    "Veterinary",
    "Architecture",
    "Law",
    "Commerce",
    "Management",
    "Economics",
    "Arts & Humanities",
    "Science",
    "Computer Science/IT",
    "Education",
    "Social Sciences",
    "Fine Arts",
    "Design",
    "Aerospace/Aviation",
    "Maritime",
    "Polytechnic/Technical",
    "Vocational/Skill Development",
    "Multidisciplinary",
    "Other",
]

# School-level entity types that do not require higher-ed specialization domains unless opted in
SCHOOL_LEVEL_TYPES = {
    "Preschool/Early Childhood",
    "Primary School",
    "Middle/Upper Primary",
    "High/Secondary School",
}


def is_other_value(val: Optional[str]) -> bool:
    if not val:
        return False
    return val.strip().lower() == "other"


def determine_structural_archetype(
    entity_category: Optional[str],
    entity_type: Optional[str],
    education_entity_type: Optional[str],
) -> str:
    """
    Determine which of the 3 structural hierarchies applies:
    1. 'organization_group': Organization/Group -> Institutions -> Departments -> Programs -> Data/Signals
    2. 'university': University -> Campuses/Schools/Faculties -> Departments -> Programs -> Data/Signals
    3. 'institution': Institution -> Departments -> Programs -> Data/Signals
    """
    cat = (entity_category or "").strip().lower()
    etype = (entity_type or "").strip().lower()
    edutype = (education_entity_type or "").strip().lower()

    if edutype == "university" or etype == "university":
        return "university"

    if cat in (
        "educational_group_network",
        "nonprofit_trust_society_foundation",
        "government_public_body",
        "private_organization",
    ) or etype in (
        "educational_group_network",
        "nonprofit_trust_society",
        "government_public_body",
        "private_organization",
    ) or edutype in (
        "school group",
        "college group",
        "university system",
        "educational trust",
        "educational society/sangha",
        "education network",
    ):
        return "organization_group"

    return "institution"


def get_applicable_profile_fields(
    entity_category: Optional[str],
    entity_type: Optional[str],
    education_entity_type: Optional[str],
) -> Dict[str, Any]:
    """
    Compute which profile and child-entity fields are applicable to the selected entity
    so neither API nor UI forces irrelevant options.
    """
    archetype = determine_structural_archetype(entity_category, entity_type, education_entity_type)
    edutype = (education_entity_type or "").strip()
    is_school = edutype in SCHOOL_LEVEL_TYPES
    is_university = archetype == "university"
    is_org_group = archetype == "organization_group"

    if is_org_group:
        child_hierarchy = ["institutions", "departments", "programs", "signals"]
        allowed_child_node_types = ["department", "program", "other"]
    elif is_university:
        child_hierarchy = ["campuses_schools_faculties", "departments", "programs", "signals"]
        allowed_child_node_types = ["campus_school_faculty", "department", "program", "other"]
    else:
        child_hierarchy = ["departments", "programs", "signals"]
        allowed_child_node_types = ["department", "program", "other"]

    return {
        "structural_archetype": archetype,
        "show_university_type": is_university or edutype == "University System",
        "show_academic_domains": not is_school,
        "show_accreditation_grade": not is_org_group and not is_school,
        "show_affiliation_details": edutype in ("College", "Vocational/Technical School", "PUC/Pre-University"),
        "show_child_institutions_manager": is_org_group,
        "show_campus_school_faculty_layer": is_university,
        "child_hierarchy_path": child_hierarchy,
        "allowed_child_node_types": allowed_child_node_types,
    }


def get_full_taxonomy_catalog() -> Dict[str, Any]:
    return {
        "entity_categories": ENTITY_CATEGORIES,
        "governance_by_category": GOVERNANCE_BY_CATEGORY,
        "entity_types_by_category": ENTITY_TYPES_BY_CATEGORY,
        "education_entity_types_institution": EDUCATION_ENTITY_TYPES_INSTITUTION,
        "organization_group_structures": ORGANIZATION_GROUP_STRUCTURES,
        "university_types": UNIVERSITY_TYPES,
        "academic_domains": ACADEMIC_DOMAINS,
    }
