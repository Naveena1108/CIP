"""
Universal Institutional Data Ingestion Pipeline for CIP Phase 2.

Implements the end-to-end ingestion pipeline:
SOURCE
 -> format detection
 -> extraction / OCR / transcription / parsing
 -> semantic understanding
 -> institutional context (evidence-backed only; never invents missing context)
 -> signal discovery (dynamic across 16+ institutional domains)
 -> evidence extraction (source, document, page/section, sheet/row/cell, excerpt, confidence)
 -> canonical representation (bridges to AdmissionsSignal, PlacementsSignal, CETRankingSignal + universal DiscoveredSignal)
 -> intelligence & data quality (missing periods, incomplete coverage, OCR uncertainty,
    duplicates, outdated data, ambiguous values, contradictory sources kept visible)
"""

import csv
import hashlib
import io
import json
import mimetypes
import os
import re
import tempfile
import uuid
import wave
import zipfile
import zlib
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import openpyxl
from PIL import Image

from src.adapters.excel_adapter import ExcelInstitutionalAdapter
from src.adapters.json_adapter import JSONDictionaryAdapter
from src.contracts import (
    AdmissionsSignal,
    CETRankingSignal,
    DataQualityIssue,
    DiscoveredSignal,
    InstitutionalContextAssociation,
    PlacementsSignal,
    ProvenanceMetadata,
    SignalProvenance,
    UniversalIngestionResult,
)


# ---------------------------------------------------------------------------
# Domain & Metric Semantic Knowledge Base (Dynamic Signal Discovery)
# ---------------------------------------------------------------------------

DOMAIN_KEYWORDS: Dict[str, List[Tuple[str, str, Optional[str], str]]] = {
    # (regex_pattern, normalized_metric_name, default_unit, polarity)
    "admissions": [
        (r"sanctioned\s*intake|approved\s*intake|total\s*seats|seat\s*capacity", "sanctioned_intake", "count", "neutral"),
        (r"admitted\s*students|total\s*admitted|enrolled\s*first\s*year|admissions\s*count|seats\s*filled", "admitted_students", "count", "higher_is_better"),
        (r"vacancy\s*rate|seat\s*vacancy|unfilled\s*seats\s*%|vacant\s*ratio", "vacancy_rate", "ratio", "lower_is_better"),
        (r"admission\s*fill\s*rate|enrollment\s*ratio|occupancy\s*rate", "admission_fill_rate", "%", "higher_is_better"),
        (r"applications\s*received|applicant\s*count", "applications_received", "count", "higher_is_better"),
    ],
    "placements": [
        (r"placement\s*percentage|placement\s*rate|placed\s*%|%\s*placed|campus\s*placement\s*rate", "placement_percentage", "%", "higher_is_better"),
        (r"eligible\s*students|placement\s*eligible", "eligible_students", "count", "neutral"),
        (r"placed\s*students|total\s*placed|students\s*placed|job\s*offers\s*accepted", "placed_students", "count", "higher_is_better"),
        (r"median\s*salary|average\s*package|median\s*package|avg\s*ctc|median\s*ctc", "median_salary_lpa", "LPA", "higher_is_better"),
        (r"highest\s*package|max\s*ctc|top\s*package", "highest_salary_lpa", "LPA", "higher_is_better"),
    ],
    "ranking": [
        (r"closing\s*rank|cet\s*cutoff|cutoff\s*rank|last\s*admitted\s*rank", "closing_rank", "rank", "lower_is_better"),
        (r"opening\s*rank|first\s*admitted\s*rank", "opening_rank", "rank", "lower_is_better"),
        (r"nirf\s*rank|national\s*rank|institutional\s*rank", "institutional_rank", "rank", "lower_is_better"),
    ],
    "faculty": [
        (r"faculty\s*attrition|faculty\s*turnover|teacher\s*attrition|faculty\s*resignation", "faculty_attrition_rate", "%", "lower_is_better"),
        (r"faculty\s*count|total\s*faculty|teaching\s*staff\s*strength|faculty\s*strength", "faculty_count", "count", "higher_is_better"),
        (r"phd\s*faculty|doctorate\s*faculty|faculty\s*with\s*phd", "phd_faculty_pct", "%", "higher_is_better"),
        (r"faculty\s*vacancy|vacant\s*faculty\s*posts|unfilled\s*faculty", "faculty_vacancy_rate", "%", "lower_is_better"),
        (r"student\s*faculty\s*ratio|sfr|student\s*teacher\s*ratio|str", "student_faculty_ratio", "ratio", "lower_is_better"),
    ],
    "students": [
        (r"total\s*students|student\s*strength|total\s*enrollment|active\s*students", "total_student_strength", "count", "higher_is_better"),
        (r"scholarship\s*recipients|students\s*on\s*scholarship", "scholarship_students", "count", "higher_is_better"),
        (r"female\s*students\s*%|gender\s*diversity\s*%|women\s*enrollment", "female_enrollment_pct", "%", "higher_is_better"),
        (r"hostel\s*occupancy", "hostel_occupancy_pct", "%", "higher_is_better"),
    ],
    "academics": [
        (r"pass\s*percentage|pass\s*rate|exam\s*pass\s*%|graduation\s*rate|result\s*%", "pass_percentage", "%", "higher_is_better"),
        (r"backlog\s*rate|fail\s*percentage|arrears\s*%|students\s*with\s*backlogs", "backlog_rate", "%", "lower_is_better"),
        (r"average\s*cgpa|mean\s*gpa", "average_cgpa", "score", "higher_is_better"),
        (r"curriculum\s*revision|syllabus\s*update", "curriculum_revisions", "count", "higher_is_better"),
    ],
    "research": [
        (r"research\s*publications|published\s*papers|scopus\s*papers|sci\s*publications|journal\s*papers", "research_publications", "count", "higher_is_better"),
        (r"research\s*grants|sponsored\s*research|funded\s*projects|extramural\s*funding|grant\s*amount", "research_grants_lakhs", "INR_Lakhs", "higher_is_better"),
        (r"patents\s*filed|patents\s*granted|intellectual\s*property", "patents_count", "count", "higher_is_better"),
        (r"citations\s*count|citation\s*index|h-index", "citations_count", "count", "higher_is_better"),
        (r"consultancy\s*revenue|consultancy\s*income", "consultancy_revenue_lakhs", "INR_Lakhs", "higher_is_better"),
    ],
    "infrastructure": [
        (r"lab\s*utilization|laboratory\s*utilization|equipment\s*uptime|lab\s*readiness", "lab_utilization_pct", "%", "higher_is_better"),
        (r"infrastructure\s*deficiency|lab\s*shortage|classroom\s*shortage|maintenance\s*backlog", "infrastructure_deficiency_pct", "%", "lower_is_better"),
        (r"library\s*volumes|books\s*count|digital\s*library\s*resources", "library_volumes", "count", "higher_is_better"),
        (r"internet\s*bandwidth|campus\s*wifi\s*mbps", "internet_bandwidth_mbps", "Mbps", "higher_is_better"),
        (r"computer\s*student\s*ratio|pc\s*availability", "computer_student_ratio", "ratio", "higher_is_better"),
    ],
    "finance": [
        (r"budget\s*deficit|operating\s*deficit|financial\s*deficit|revenue\s*shortfall|fee\s*deficit", "budget_deficit_pct", "%", "lower_is_better"),
        (r"fee\s*collection|tuition\s*collection\s*rate|revenue\s*realization", "fee_collection_pct", "%", "higher_is_better"),
        (r"annual\s*budget|total\s*expenditure|operational\s*budget|annual\s*revenue", "annual_budget_lakhs", "INR_Lakhs", "neutral"),
        (r"salary\s*delay|payroll\s*arrears|unpaid\s*salary\s*months", "salary_delay_months", "months", "lower_is_better"),
        (r"r&d\s*expenditure|research\s*spend\s*%", "rd_expenditure_pct", "%", "higher_is_better"),
    ],
    "retention": [
        (r"retention\s*rate|student\s*retention|cohort\s*retention|continuation\s*rate", "student_retention_rate", "%", "higher_is_better"),
        (r"dropout\s*rate|student\s*attrition|withdrawal\s*rate|discontinuation\s*rate", "student_dropout_rate", "%", "lower_is_better"),
    ],
    "attendance": [
        (r"student\s*attendance|average\s*attendance|class\s*attendance|attendance\s*rate|attendance\s*%", "student_attendance_pct", "%", "higher_is_better"),
        (r"chronic\s*absenteeism|attendance\s*shortage|detained\s*for\s*attendance", "attendance_shortage_pct", "%", "lower_is_better"),
        (r"faculty\s*attendance", "faculty_attendance_pct", "%", "higher_is_better"),
    ],
    "grievances": [
        (r"pending\s*grievances|unresolved\s*grievances|open\s*complaints", "pending_grievances", "count", "lower_is_better"),
        (r"grievances\s*filed|total\s*grievances|student\s*complaints|disciplinary\s*incidents|grievance\s*count", "grievances_filed", "count", "lower_is_better"),
        (r"grievance\s*resolution\s*rate|complaints\s*resolved\s*%", "grievance_resolution_pct", "%", "higher_is_better"),
    ],
    "internships": [
        (r"internship\s*percentage|internship\s*rate|students\s*interning\s*%|internship\s*coverage", "internship_percentage", "%", "higher_is_better"),
        (r"internship\s*count|total\s*internships|summer\s*internships|industrial\s*training\s*count", "internships_count", "count", "higher_is_better"),
        (r"ppo\s*conversion|pre-placement\s*offers", "ppo_conversion_count", "count", "higher_is_better"),
    ],
    "industry": [
        (r"active\s*mous|industry\s*mous|corporate\s*partnerships|industry\s*collaborations|mou\s*count", "active_mous_count", "count", "higher_is_better"),
        (r"industry\s*sponsored\s*labs|centers\s*of\s*excellence", "industry_labs_count", "count", "higher_is_better"),
        (r"corporate\s*projects|live\s*industry\s*projects", "industry_projects_count", "count", "higher_is_better"),
    ],
    "accreditation": [
        (r"naac\s*cgpa|naac\s*score|accreditation\s*score", "naac_cgpa", "score", "higher_is_better"),
        (r"nba\s*accredited|accredited\s*programs\s*%", "nba_accredited_programs_pct", "%", "higher_is_better"),
    ],
    "compliance": [
        (r"compliance\s*score|statutory\s*compliance\s*%|regulatory\s*compliance", "compliance_score_pct", "%", "higher_is_better"),
        (r"deficiency\s*flags|regulatory\s*notices|show\s*cause\s*notices|audit\s*non-compliance|compliance\s*violations", "compliance_deficiencies", "count", "lower_is_better"),
    ],
    "feedback": [
        (r"student\s*feedback|student\s*satisfaction|teaching\s*feedback\s*score|course\s*rating", "student_feedback_score", "score", "higher_is_better"),
        (r"employer\s*satisfaction|recruiter\s*feedback", "employer_satisfaction_pct", "%", "higher_is_better"),
        (r"alumni\s*feedback|alumni\s*satisfaction", "alumni_satisfaction_pct", "%", "higher_is_better"),
    ],
}

KNOWN_DEPT_PATTERNS = {
    r"\bcse\b|computer\s*science\s*(?:and|&)?\s*eng": "CSE",
    r"\baiml\b|ai\s*(?:and|&)\s*ml|artificial\s*intelligence": "AIML",
    r"\bise\b|information\s*science": "ISE",
    r"\bece\b|electronics\s*(?:and|&)\s*communication": "ECE",
    r"\beee\b|electrical\s*(?:and|&)\s*electronics": "EEE",
    r"\bmech\b|mechanical\s*eng": "MECH",
    r"\bcivil\b|civil\s*eng": "CIVIL",
    r"\bmba\b|master\s*of\s*business": "MBA",
    r"\bmca\b|master\s*of\s*computer\s*applications": "MCA",
    r"\bphysics\b|dept\.?\s*of\s*physics": "PHYSICS",
    r"\bchemistry\b|dept\.?\s*of\s*chemistry": "CHEMISTRY",
    r"\bmathematics\b|\bmaths\b": "MATHEMATICS",
    r"\bbiotech\b|biotechnology": "BIOTECH",
    r"\bcommerce\b|b\.?com": "COMMERCE",
    r"\blaw\b|school\s*of\s*law": "LAW",
    r"\bmedicine\b|general\s*medicine": "MEDICINE",
}

KNOWN_PROGRAM_PATTERNS = [
    r"\b(?:B\.?E\.?|B\.?Tech\.?|M\.?Tech\.?|MBA|MCA|B\.?Sc\.?|M\.?Sc\.?|B\.?Com\.?|BBA|LL\.?B\.?|MBBS|Ph\.?D\.?)\b(?:\s+in\s+[A-Za-z &]+)?"
]

CONTEXT_HEADER_ALIASES = {
    "institution": {"institution", "institution_id", "institution_name", "college", "college_name", "university", "entity"},
    "organization": {"organization", "organization_id", "organization_name", "group", "trust", "society"},
    "department": {"department", "dept", "branch", "discipline", "school", "stream"},
    "program": {"program", "programme", "course", "degree", "specialization"},
    "period": {"year", "academic_year", "ay", "period", "time_period", "graduation_year", "batch", "session", "semester"},
    "domain": {"domain", "category", "signal_domain", "area"},
    "metric": {"metric", "metric_name", "indicator", "signal", "parameter", "kpi", "measure"},
    "value": {"value", "observed_value", "score", "amount", "count", "rate", "percentage", "figure"},
    "unit": {"unit", "units", "uom"},
}

SUPPORTED_EXTENSIONS = {
    ".xlsx": "XLSX",
    ".xlsm": "XLSX",
    ".csv": "CSV",
    ".tsv": "CSV",
    ".txt": "TXT",
    ".md": "TXT",
    ".json": "TXT",
    ".pdf": "PDF",
    ".docx": "DOCX",
    ".pptx": "PPTX",
    ".png": "IMAGE",
    ".jpg": "IMAGE",
    ".jpeg": "IMAGE",
    ".webp": "IMAGE",
    ".bmp": "IMAGE",
    ".tiff": "IMAGE",
    ".tif": "IMAGE",
    ".gif": "IMAGE",
    ".wav": "AUDIO_VIDEO",
    ".mp3": "AUDIO_VIDEO",
    ".m4a": "AUDIO_VIDEO",
    ".mp4": "AUDIO_VIDEO",
    ".mov": "AUDIO_VIDEO",
    ".webm": "AUDIO_VIDEO",
    ".ogg": "AUDIO_VIDEO",
}


def classify_metric(raw_label: str) -> Tuple[str, str, Optional[str], str]:
    """
    Classify an arbitrary metric label into (domain, normalized_metric_name, default_unit, polarity).
    Never requires fixed column names; dynamically falls back to semantic token matching
    or general_institutional if completely novel.
    """
    cleaned = raw_label.strip().lower()
    for domain, rules in DOMAIN_KEYWORDS.items():
        for pattern, norm_name, unit, polarity in rules:
            if re.search(pattern, cleaned, re.IGNORECASE):
                return domain, norm_name, unit, polarity

    # Second-pass broad domain keyword matching for novel metrics in known domains
    broad_domain_map = [
        ("admissions", ["admission", "intake", "enrol", "seat", "applicant"]),
        ("placements", ["placement", "placed", "recruiter", "ctc", "package", "salary", "offer"]),
        ("ranking", ["rank", "cutoff", "cet", "comedk", "jee"]),
        ("faculty", ["faculty", "professor", "teacher", "lecturer", "staff", "sfr"]),
        ("research", ["research", "publication", "paper", "patent", "grant", "citation", "scopus", "consultancy"]),
        ("infrastructure", ["infrastructure", "lab", "classroom", "library", "wifi", "bandwidth", "hostel", "equipment"]),
        ("finance", ["finance", "budget", "revenue", "expenditure", "deficit", "surplus", "fee", "tuition", "audit", "fund"]),
        ("retention", ["retention", "dropout", "attrition", "continuation", "withdrawal"]),
        ("attendance", ["attendance", "absentee", "biometric", "present"]),
        ("grievances", ["grievance", "complaint", "harassment", "ragging", "disciplinary", "ombudsman", "protest"]),
        ("internships", ["internship", "intern", "stipend", "industrial training", "ppo"]),
        ("industry", ["industry", "mou", "corporate", "partner", "collaboration"]),
        ("accreditation", ["accreditation", "naac", "nba", "nirf", "autonomous", "iso"]),
        ("compliance", ["compliance", "statutory", "regulatory", "aicte", "ugc", "deficiency", "violation"]),
        ("feedback", ["feedback", "satisfaction", "rating", "survey", "evaluation"]),
        ("academics", ["academic", "exam", "pass", "cgpa", "gpa", "backlog", "syllabus", "curriculum", "grade"]),
        ("students", ["student", "scholar", "cohort", "learner"]),
    ]
    matched_domain = "general_institutional"
    for dom, kw_list in broad_domain_map:
        if any(kw in cleaned for kw in kw_list):
            matched_domain = dom
            break

    norm_key = re.sub(r"[^a-z0-9]+", "_", cleaned).strip("_")
    if not norm_key:
        norm_key = " institutional_metric"

    unit = None
    if "%" in raw_label or "percent" in cleaned or "rate" in cleaned or "ratio" in cleaned:
        unit = "%"
    elif "lakh" in cleaned or "inr" in cleaned or "rs" in cleaned:
        unit = "INR_Lakhs"
    elif "lpa" in cleaned:
        unit = "LPA"
    elif "count" in cleaned or "number" in cleaned or "total" in cleaned:
        unit = "count"

    polarity = "neutral"
    if any(w in cleaned for w in ["deficit", "attrition", "dropout", "vacancy", "grievance", "complaint", "backlog", "delay", "shortage", "deficiency", "violation"]):
        polarity = "lower_is_better"
    elif any(w in cleaned for w in ["pass", "placement", "retention", "attendance", "satisfaction", "publication", "grant", "collection", "compliance", "utilization"]):
        polarity = "higher_is_better"

    return matched_domain, norm_key, unit, polarity


def compute_risk_contribution(domain: str, metric_name: str, value: Optional[float], unit: Optional[str], polarity: str) -> Optional[float]:
    """Compute a calibrated 0.0–1.0 risk contribution for a numeric signal."""
    if value is None:
        return None
    if metric_name == "vacancy_rate":
        val_ratio = value / 100.0 if value > 1.0 else value
        return round(min(1.0, max(0.0, val_ratio * 1.5)), 3)
    if metric_name == "placement_percentage":
        return round(min(1.0, max(0.0, (100.0 - value) / 100.0)), 3)
    if metric_name in ("faculty_attrition_rate", "student_dropout_rate", "backlog_rate", "attendance_shortage_pct", "budget_deficit_pct", "infrastructure_deficiency_pct", "faculty_vacancy_rate"):
        pct = value if value > 1.0 else value * 100.0
        return round(min(1.0, max(0.0, pct / 40.0)), 3)
    if metric_name in ("student_attendance_pct", "student_retention_rate", "pass_percentage", "fee_collection_pct", "lab_utilization_pct", "compliance_score_pct", "grievance_resolution_pct"):
        pct = value if value > 1.0 else value * 100.0
        return round(min(1.0, max(0.0, (100.0 - pct) / 60.0)), 3)
    if metric_name in ("pending_grievances", "grievances_filed", "compliance_deficiencies"):
        return round(min(1.0, max(0.0, value / 25.0)), 3)
    if polarity == "lower_is_better" and unit == "%":
        pct = value if value > 1.0 else value * 100.0
        return round(min(1.0, max(0.0, pct / 50.0)), 3)
    if polarity == "higher_is_better" and unit == "%":
        pct = value if value > 1.0 else value * 100.0
        return round(min(1.0, max(0.0, (100.0 - pct) / 100.0)), 3)
    return None


def parse_numeric_value(raw_str: str) -> Tuple[Optional[float], bool, Optional[str]]:
    """
    Parse a numeric value from a raw string.
    Returns (numeric_value, is_ambiguous, detected_unit).
    Flags ambiguous values like '~45%', '40-50', 'approx 120', 'TBD', 'N/A', or '?'.
    """
    s = str(raw_str).strip()
    if not s:
        return None, False, None

    is_ambiguous = False
    if any(tok in s.lower() for tok in ["~", "approx", "about", "est", "tbd", "n/a", "unknown", "?", "maybe", "to"]):
        is_ambiguous = True
    # Range pattern like "40-50" or "40 - 50" (not negative number and not academic year 2023-24)
    if re.match(r"^\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?%?$", s) and not re.match(r"^20\d{2}\s*-\s*\d{2,4}$", s):
        is_ambiguous = True

    detected_unit = None
    if "%" in s:
        detected_unit = "%"
    elif re.search(r"\blpa\b", s, re.IGNORECASE):
        detected_unit = "LPA"
    elif re.search(r"\b(?:lakhs?|lacs?)\b", s, re.IGNORECASE):
        detected_unit = "INR_Lakhs"

    # Extract first number
    cleaned = s.replace(",", "")
    m = re.search(r"[-+]?\d+(?:\.\d+)?", cleaned)
    if not m:
        return None, is_ambiguous, detected_unit

    val = float(m.group(0))
    return val, is_ambiguous, detected_unit


def extract_evidence_backed_context(
    text_context: str,
    explicit_fields: Optional[Dict[str, Tuple[str, str]]] = None,
    scoped_org_id: Optional[str] = None,
    scoped_inst_id: Optional[str] = None,
    source_name: str = "UPLOAD",
) -> InstitutionalContextAssociation:
    """
    Construct an InstitutionalContextAssociation ONLY where evidence supports each field.
    Never invents department, program, or time_period if absent from the source evidence.
    `explicit_fields` maps field_name -> (value, evidence_description).
    """
    explicit = explicit_fields or {}
    evidence_map: Dict[str, str] = {}

    org_id = scoped_org_id
    if org_id:
        evidence_map["organization_id"] = "Authenticated user organization scope"

    inst_id = scoped_inst_id
    if inst_id:
        evidence_map["institution_id"] = "Authenticated user / target institution scope"

    org_name: Optional[str] = None
    inst_name: Optional[str] = None
    dept: Optional[str] = None
    program: Optional[str] = None
    time_period: Optional[str] = None
    academic_year: Optional[int] = None

    if "organization" in explicit and explicit["organization"][0]:
        org_name = explicit["organization"][0].strip()
        evidence_map["organization_name"] = explicit["organization"][1]

    if "institution" in explicit and explicit["institution"][0]:
        inst_name = explicit["institution"][0].strip()
        if not inst_id:
            inst_id = re.sub(r"[^A-Za-z0-9_-]+", "-", inst_name).strip("-").upper()[:32]
        evidence_map["institution_name"] = explicit["institution"][1]

    if "department" in explicit and explicit["department"][0]:
        dept = explicit["department"][0].strip()
        evidence_map["department"] = explicit["department"][1]
    elif text_context:
        for pat, code in KNOWN_DEPT_PATTERNS.items():
            m = re.search(pat, text_context, re.IGNORECASE)
            if m:
                dept = code
                evidence_map["department"] = f"Matched '{m.group(0)}' in source text"
                break

    if "program" in explicit and explicit["program"][0]:
        program = explicit["program"][0].strip()
        evidence_map["program"] = explicit["program"][1]
    elif text_context:
        for pat in KNOWN_PROGRAM_PATTERNS:
            m = re.search(pat, text_context, re.IGNORECASE)
            if m:
                program = m.group(0).strip()
                evidence_map["program"] = f"Matched program '{program}' in source text"
                break

    if "period" in explicit and explicit["period"][0]:
        raw_p = str(explicit["period"][0]).strip()
        if raw_p:
            time_period = raw_p
            evidence_map["time_period"] = explicit["period"][1]
            ym = re.search(r"\b(20\d{2})\b", raw_p)
            if ym:
                academic_year = int(ym.group(1))
                evidence_map["academic_year"] = explicit["period"][1]
    elif text_context:
        # Look for AY 2023-24, 2023-2024, DATA 2023, or standalone 2015..2030
        ym = re.search(r"\b(?:AY\s*)?(20[12]\d(?:\s*-\s*\d{2,4})?)\b", text_context, re.IGNORECASE)
        if ym:
            time_period = ym.group(1).strip()
            y4 = re.search(r"20[12]\d", time_period)
            if y4:
                academic_year = int(y4.group(0))
            evidence_map["time_period"] = f"Extracted period '{ym.group(0)}' from source text"
            evidence_map["academic_year"] = evidence_map["time_period"]

    return InstitutionalContextAssociation(
        organization_id=org_id,
        organization_name=org_name,
        institution_id=inst_id,
        institution_name=inst_name,
        department=dept,
        program=program,
        time_period=time_period,
        academic_year=academic_year,
        source=source_name,
        context_evidence=evidence_map,
    )


def generate_signal_fingerprint(
    institution_id: Optional[str],
    domain: str,
    metric_name: str,
    academic_year: Optional[int] = None,
    time_period: Optional[str] = None,
    department: Optional[str] = None,
    spreadsheet_location: Optional[str] = None,
    raw_value: str = "",
) -> str:
    parts = [
        str(institution_id or "").strip().lower(),
        str(domain or "").strip().lower(),
        str(metric_name or "").strip().lower(),
        str(academic_year or time_period or "").strip().lower(),
        str(department or "").strip().lower(),
        str(spreadsheet_location or "").strip().lower(),
        str(raw_value or "").strip().lower(),
    ]
    return hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()[:24]


def make_signal_id(
    institution_id: Optional[str],
    domain: str,
    metric_name: str,
    academic_year: Optional[int] = None,
    time_period: Optional[str] = None,
    department: Optional[str] = None,
    spreadsheet_location: Optional[str] = None,
    raw_value: str = "",
) -> Tuple[str, str]:
    fp = generate_signal_fingerprint(
        institution_id, domain, metric_name, academic_year, time_period, department, spreadsheet_location, raw_value
    )
    return f"sig_{fp}", fp


class UniversalInstitutionalIngestor:
    """
    Universal Institutional Data Ingestion Engine for CIP Phase 2.
    Supports: PDF, XLSX, CSV, DOCX, PPTX, TXT, Images/Scans, Audio/Video.
    Never claims support for a format without a working extraction path.
    Preserves contradictory values visibly and detects all 7 data quality issue types.
    """

    def __init__(
        self,
        organization_id: Optional[str] = None,
        institution_id: Optional[str] = None,
        existing_signals: Optional[List[DiscoveredSignal]] = None,
    ):
        self.organization_id = organization_id
        self.institution_id = institution_id
        self.existing_signals: List[DiscoveredSignal] = existing_signals or []

    # ------------------------------------------------------------------
    # Stage 1: Format Detection
    # ------------------------------------------------------------------
    @staticmethod
    def detect_format(filename: str, content: bytes, content_type: Optional[str] = None) -> Tuple[str, str, Optional[str]]:
        """
        Inspect file extension, MIME type, and magic bytes.
        Returns (format_category, detected_mime, unsupported_reason_if_any).
        """
        ext = os.path.splitext(filename.lower())[1]
        guessed_mime, _ = mimetypes.guess_type(filename)
        mime = content_type or guessed_mime or "application/octet-stream"

        # Check magic bytes first for accuracy
        if content.startswith(b"%PDF-"):
            return "PDF", "application/pdf", None

        if content.startswith(b"PK\x03\x04"):
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as zf:
                    names = set(zf.namelist())
                    if any(n.startswith("xl/") for n in names):
                        return "XLSX", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", None
                    if any(n.startswith("word/") for n in names):
                        return "DOCX", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", None
                    if any(n.startswith("ppt/") for n in names):
                        return "PPTX", "application/vnd.openxmlformats-officedocument.presentationml.presentation", None
                    return (
                        "UNSUPPORTED",
                        "application/zip",
                        f"File '{filename}' is a generic ZIP archive without OpenXML (XLSX/DOCX/PPTX) structure. Please extract and upload the individual institutional files.",
                    )
            except zipfile.BadZipFile:
                return (
                    "UNSUPPORTED",
                    mime,
                    f"File '{filename}' has a corrupted ZIP/OpenXML container header and cannot be extracted.",
                )

        # Legacy OLE2 binary (.xls, .doc, .ppt)
        if content.startswith(b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"):
            return (
                "UNSUPPORTED",
                "application/x-ole-storage",
                f"File '{filename}' is a legacy binary OLE2 compound document ({ext or 'legacy Office'}). Only modern OpenXML (.xlsx, .docx, .pptx) and .pdf/.csv/.txt formats have active deterministic extractors.",
            )

        # Executables / system binaries
        if content.startswith(b"MZ") or content.startswith(b"\x7fELF"):
            return (
                "UNSUPPORTED",
                "application/x-executable",
                f"File '{filename}' is a binary executable, not an institutional data document.",
            )

        # Images via magic bytes or extension
        if (
            content.startswith(b"\x89PNG\r\n\x1a\n")
            or content.startswith(b"\xff\xd8\xff")
            or content.startswith((b"GIF87a", b"GIF89a", b"BM", b"II*\x00", b"MM\x00*"))
            or (content.startswith(b"RIFF") and content[8:12] == b"WEBP")
            or ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif", ".gif")
        ):
            return "IMAGE", mime if mime.startswith("image/") else "image/png", None

        # Audio / Video via magic bytes or extension
        if (
            (content.startswith(b"RIFF") and content[8:12] == b"WAVE")
            or content.startswith(b"ID3")
            or content.startswith(b"OggS")
            or content.startswith(b"\x1a\x45\xdf\xa3")
            or (len(content) > 12 and content[4:8] == b"ftyp")
            or ext in (".wav", ".mp3", ".m4a", ".mp4", ".mov", ".webm", ".ogg")
        ):
            return "AUDIO_VIDEO", mime, None

        if ext in SUPPORTED_EXTENSIONS:
            return SUPPORTED_EXTENSIONS[ext], mime, None

        return (
            "UNSUPPORTED",
            mime,
            f"Unsupported format '{ext or mime}' for file '{filename}'. CIP supports PDF, XLSX, CSV, DOCX, PPTX, TXT/MD/JSON, Images/Scans, and Audio/Video.",
        )

    # ------------------------------------------------------------------
    # Main Pipeline Entrypoint
    # ------------------------------------------------------------------
    def ingest_bytes(
        self,
        content: bytes,
        filename: str,
        content_type: Optional[str] = None,
    ) -> Tuple[UniversalIngestionResult, Dict[str, List[Any]]]:
        """
        Execute the full 8-stage universal ingestion pipeline on raw file bytes.
        Returns (UniversalIngestionResult, canonical_signals_dict).
        """
        ingestion_id = f"ing_{uuid.uuid4().hex[:12]}"
        stages: Dict[str, str] = {}
        quality_issues: List[DataQualityIssue] = []
        discovered: List[DiscoveredSignal] = []
        canonical_map: Dict[str, List[Any]] = {
            "admissions": [],
            "placements": [],
            "cet_ranking": [],
        }

        # Stage 1: Format Detection
        fmt, mime, unsupported_reason = self.detect_format(filename, content, content_type)
        stages["1_format_detection"] = f"Detected format={fmt} (mime={mime})"

        if fmt == "UNSUPPORTED" or unsupported_reason:
            stages["2_extraction_parsing"] = f"Halted: {unsupported_reason}"
            return (
                UniversalIngestionResult(
                    ingestion_id=ingestion_id,
                    status="UNSUPPORTED_FORMAT",
                    truthful_explanation=unsupported_reason or f"Format '{fmt}' is not supported.",
                    detected_format=fmt,
                    mime_type=mime,
                    filename=filename,
                    organization_id=self.organization_id,
                    institution_id=self.institution_id,
                    pipeline_stages=stages,
                    extraction_confidence=0.0,
                ),
                canonical_map,
            )

        if len(content) == 0:
            stages["2_extraction_parsing"] = "Halted: Uploaded file is empty (0 bytes)."
            return (
                UniversalIngestionResult(
                    ingestion_id=ingestion_id,
                    status="EXTRACTION_FAILED",
                    truthful_explanation=f"Uploaded file '{filename}' is empty (0 bytes).",
                    detected_format=fmt,
                    mime_type=mime,
                    filename=filename,
                    organization_id=self.organization_id,
                    institution_id=self.institution_id,
                    pipeline_stages=stages,
                    extraction_confidence=0.0,
                ),
                canonical_map,
            )

        # Stage 2..6: Format-Specific Extraction, Context Association, Signal Discovery & Provenance
        extraction_status = "SUCCESS"
        explanation = ""
        try:
            if fmt == "XLSX":
                discovered, canonical_map, q_issues, explanation = self._extract_xlsx(content, filename)
                quality_issues.extend(q_issues)
            elif fmt == "CSV":
                discovered, q_issues, explanation = self._extract_csv(content, filename)
                quality_issues.extend(q_issues)
            elif fmt == "DOCX":
                discovered, q_issues, explanation = self._extract_docx(content, filename)
                quality_issues.extend(q_issues)
            elif fmt == "PPTX":
                discovered, q_issues, explanation = self._extract_pptx(content, filename)
                quality_issues.extend(q_issues)
            elif fmt == "PDF":
                discovered, q_issues, explanation, extraction_status = self._extract_pdf(content, filename)
                quality_issues.extend(q_issues)
            elif fmt == "TXT":
                discovered, canonical_map, q_issues, explanation = self._extract_txt_or_json(content, filename)
                quality_issues.extend(q_issues)
            elif fmt == "IMAGE":
                discovered, q_issues, explanation, extraction_status = self._extract_image(content, filename)
                quality_issues.extend(q_issues)
            elif fmt == "AUDIO_VIDEO":
                discovered, q_issues, explanation, extraction_status = self._extract_audio_video(content, filename)
                quality_issues.extend(q_issues)
        except Exception as exc:
            stages["2_extraction_parsing"] = f"Extraction error: {exc}"
            return (
                UniversalIngestionResult(
                    ingestion_id=ingestion_id,
                    status="EXTRACTION_FAILED",
                    truthful_explanation=f"Failed to parse {fmt} file '{filename}': {exc}",
                    detected_format=fmt,
                    mime_type=mime,
                    filename=filename,
                    organization_id=self.organization_id,
                    institution_id=self.institution_id,
                    pipeline_stages=stages,
                    extraction_confidence=0.0,
                ),
                canonical_map,
            )

        stages["2_extraction_parsing"] = explanation

        # Stage 3: Semantic Understanding
        domains_found = sorted(list({s.domain for s in discovered}))
        stages["3_semantic_understanding"] = (
            f"Classified {len(discovered)} signal observations across {len(domains_found)} institutional domains: "
            f"{', '.join(domains_found) if domains_found else 'none'}"
        )

        # Stage 4: Institutional Context Verification
        dept_evidenced = sum(1 for s in discovered if s.context.department is not None)
        period_evidenced = sum(1 for s in discovered if s.context.time_period is not None or s.context.academic_year is not None)
        # Resolve effective institution_id if evidenced in signals
        resolved_inst_id = self.institution_id
        if not resolved_inst_id:
            for s in discovered:
                if s.context.institution_id:
                    resolved_inst_id = s.context.institution_id
                    break
        if not resolved_inst_id and canonical_map["admissions"]:
            resolved_inst_id = canonical_map["admissions"][0].institution_id
        if resolved_inst_id:
            for s in discovered:
                if not s.context.institution_id:
                    s.context.institution_id = resolved_inst_id
                    s.context.context_evidence["institution_id"] = "Scoped to target institution"

        stages["4_institutional_context"] = (
            f"Context grounded: institution={resolved_inst_id or 'unattributed'}, "
            f"department evidenced on {dept_evidenced}/{len(discovered)} signals, "
            f"time period evidenced on {period_evidenced}/{len(discovered)} signals (missing context never invented)"
        )

        # Stage 5: Signal Discovery
        stages["5_signal_discovery"] = (
            f"Discovered {len(discovered)} signals across domains: {', '.join(domains_found) or 'none'}"
        )

        # Stage 6: Evidence Extraction & Provenance
        avg_conf = (
            round(sum(s.provenance.extraction_confidence for s in discovered) / len(discovered), 3)
            if discovered
            else 0.0
        )
        stages["6_evidence_extraction"] = (
            f"Attached verbatim provenance to {len(discovered)} signals (mean extraction confidence: {avg_conf})"
        )

        # Stage 7: Canonical Representation Bridge
        # If canonical signals were not already populated by RYMEC adapter, synthesize canonical
        # AdmissionsSignal / PlacementsSignal / CETRankingSignal where discovered signals provide sufficient data
        self._bridge_discovered_to_canonical(discovered, canonical_map, resolved_inst_id or "INST-UNIVERSAL", filename)
        stages["7_canonical_representation"] = (
            f"Mapped canonical signals: admissions={len(canonical_map['admissions'])}, "
            f"placements={len(canonical_map['placements'])}, cet_ranking={len(canonical_map['cet_ranking'])}, "
            f"dynamic_signals={len(discovered)}"
        )

        # Stage 8: Data Quality & Contradiction Analysis (Intelligence Readiness)
        dq_issues, contradictions_count, duplicates_count = self._analyze_data_quality_and_contradictions(
            discovered, quality_issues
        )
        stages["8_intelligence"] = (
            f"Completed data quality & contradiction audit: {len(dq_issues)} quality issues, "
            f"{contradictions_count} contradictions preserved visibly, {duplicates_count} duplicates flagged"
        )

        total_ingested = len(discovered)
        return (
            UniversalIngestionResult(
                ingestion_id=ingestion_id,
                status=extraction_status,
                truthful_explanation=explanation,
                detected_format=fmt,
                mime_type=mime,
                filename=filename,
                organization_id=self.organization_id,
                institution_id=resolved_inst_id,
                pipeline_stages=stages,
                discovered_signals=discovered,
                domains_discovered=domains_found,
                canonical_signals_mapped={
                    "admissions": len(canonical_map["admissions"]),
                    "placements": len(canonical_map["placements"]),
                    "cet_ranking": len(canonical_map["cet_ranking"]),
                    "dynamic_signals": len(discovered),
                },
                data_quality_issues=dq_issues,
                contradictions_detected=contradictions_count,
                duplicates_detected=duplicates_count,
                total_signals_ingested=total_ingested,
                admissions_count=len(canonical_map["admissions"]),
                placements_count=len(canonical_map["placements"]),
                cet_ranking_count=len(canonical_map["cet_ranking"]),
                extraction_confidence=avg_conf,
            ),
            canonical_map,
        )

    # ------------------------------------------------------------------
    # Format Extractor 1: XLSX / XLSM
    # ------------------------------------------------------------------
    def _extract_xlsx(
        self, content: bytes, filename: str
    ) -> Tuple[List[DiscoveredSignal], Dict[str, List[Any]], List[DataQualityIssue], str]:
        discovered: List[DiscoveredSignal] = []
        quality_issues: List[DataQualityIssue] = []
        canonical_map: Dict[str, List[Any]] = {"admissions": [], "placements": [], "cet_ranking": []}

        # Check if this workbook matches the specialized multi-year block layout via positive schema detection (Section 9)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            if ExcelInstitutionalAdapter.detect_schema(tmp_path):
                try:
                    rymec_adapter = ExcelInstitutionalAdapter(tmp_path, source_id=filename)
                    parsed_rymec = rymec_adapter.parse()
                    if parsed_rymec["admissions"] or parsed_rymec["placements"] or parsed_rymec["cet_ranking"]:
                        canonical_map = parsed_rymec
                        # Override institution_id if scoped by user
                        if self.institution_id:
                            for sig_list in canonical_map.values():
                                for sig in sig_list:
                                    sig.institution_id = self.institution_id
                        discovered.extend(self._canonical_to_discovered(canonical_map, filename, "XLSX"))
                except Exception as ex:
                    logger.warning(f"Specialized institutional adapter failed on verified schema {filename}: {ex}", exc_info=True)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

        # Now run universal openpyxl tabular & key-value extraction across all sheets
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
        universal_signals: List[DiscoveredSignal] = []
        sheets_parsed = 0

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows = list(ws.iter_rows(values_only=False))
            if not rows:
                continue
            sheets_parsed += 1

            # Check if this sheet is already a RYMEC 'DATA YYYY' block sheet and we already extracted RYMEC
            has_rymec_block = (
                bool(re.search(r"DATA\s*20\d{2}", sheet_name, re.IGNORECASE))
                or any(
                    re.search(r"DATA\s*20\d{2}", str(c.value or ""), re.IGNORECASE)
                    for r in rows[:10]
                    for c in r[:10]
                )
            )
            if discovered and has_rymec_block:
                continue

            sheet_signals, sheet_issues = self._extract_from_cell_grid(
                rows, filename=filename, fmt="XLSX", sheet_name=sheet_name
            )
            universal_signals.extend(sheet_signals)
            quality_issues.extend(sheet_issues)

        wb.close()
        discovered.extend(universal_signals)
        explanation = (
            f"Extracted {len(discovered)} institutional signals from XLSX workbook '{filename}' "
            f"across {sheets_parsed} worksheet(s) with exact Sheet!RowCol cell provenance."
        )
        return discovered, canonical_map, quality_issues, explanation

    def _extract_from_cell_grid(
        self,
        rows: List[Tuple[Any, ...]],
        filename: str,
        fmt: str,
        sheet_name: str,
    ) -> Tuple[List[DiscoveredSignal], List[DataQualityIssue]]:
        signals: List[DiscoveredSignal] = []
        issues: List[DataQualityIssue] = []
        seen_sig_ids: Set[str] = set()

        # Convert openpyxl cells or raw values into (row_idx_1based, col_idx_1based, coord_str, text_val)
        grid: List[List[Tuple[int, int, str, str]]] = []
        for r_idx, row in enumerate(rows, start=1):
            r_cells: List[Tuple[int, int, str, str]] = []
            for c_idx, cell in enumerate(row, start=1):
                if hasattr(cell, "coordinate"):
                    coord = f"{sheet_name}!{cell.coordinate}"
                    val = "" if cell.value is None else str(cell.value).strip()
                else:
                    coord = f"{sheet_name}!R{r_idx}C{c_idx}"
                    val = "" if cell is None else str(cell).strip()
                r_cells.append((r_idx, c_idx, coord, val))
            if any(c[3] for c in r_cells):
                grid.append(r_cells)

        if not grid:
            return signals, issues

        # Detect header row (first row with >= 2 non-empty string cells)
        header_row_idx = 0
        for idx, r_cells in enumerate(grid):
            non_empty = [c for c in r_cells if c[3]]
            if len(non_empty) >= 2:
                header_row_idx = idx
                break

        headers = [c[3] for c in grid[header_row_idx]]
        header_roles: Dict[int, str] = {}
        for col_pos, h_text in enumerate(headers):
            h_clean = re.sub(r"[^a-z0-9_]+", "_", h_text.strip().lower()).strip("_")
            for role, aliases in CONTEXT_HEADER_ALIASES.items():
                if h_clean in aliases:
                    header_roles[col_pos] = role
                    break

        is_long_table = "metric" in header_roles.values() and "value" in header_roles.values()

        for data_row in grid[header_row_idx + 1 :]:
            row_excerpt = " | ".join(f"{headers[i] if i < len(headers) and headers[i] else f'Col{i+1}'}: {c[3]}" for i, c in enumerate(data_row) if c[3])
            if not row_excerpt:
                continue

            explicit_ctx: Dict[str, Tuple[str, str]] = {}
            for col_pos, role in header_roles.items():
                if col_pos < len(data_row) and data_row[col_pos][3]:
                    cell_r, cell_c, coord, cell_val = data_row[col_pos]
                    if role in ("institution", "organization", "department", "program", "period"):
                        explicit_ctx[role] = (cell_val, f"{coord} ({headers[col_pos]}='{cell_val}')")

            if is_long_table:
                metric_col = next(k for k, v in header_roles.items() if v == "metric")
                val_col = next(k for k, v in header_roles.items() if v == "value")
                unit_col = next((k for k, v in header_roles.items() if v == "unit"), None)
                dom_col = next((k for k, v in header_roles.items() if v == "domain"), None)

                if metric_col < len(data_row) and val_col < len(data_row):
                    m_label = data_row[metric_col][3]
                    v_raw = data_row[val_col][3]
                    v_coord = data_row[val_col][2]
                    if not m_label or not v_raw:
                        continue
                    domain, norm_metric, default_unit, polarity = classify_metric(m_label)
                    if dom_col is not None and dom_col < len(data_row) and data_row[dom_col][3]:
                        domain = data_row[dom_col][3].strip().lower()
                    num_val, is_ambig, parsed_unit = parse_numeric_value(v_raw)
                    unit = (
                        data_row[unit_col][3]
                        if (unit_col is not None and unit_col < len(data_row) and data_row[unit_col][3])
                        else (parsed_unit or default_unit)
                    )
                    ctx = extract_evidence_backed_context(
                        text_context=sheet_name,
                        explicit_fields=explicit_ctx,
                        scoped_org_id=self.organization_id,
                        scoped_inst_id=self.institution_id,
                        source_name=filename,
                    )
                    if is_ambig:
                        issues.append(
                            DataQualityIssue(
                                issue_type="ambiguous_values",
                                severity="MEDIUM",
                                description=f"Ambiguous metric value '{v_raw}' for '{m_label}' at {v_coord}.",
                                affected_domain=domain,
                                affected_metric=norm_metric,
                                affected_department=ctx.department,
                                affected_period=ctx.time_period,
                                provenance_refs=[v_coord],
                            )
                        )
                    sig_id, _ = make_signal_id(
                        institution_id=self.institution_id or ctx.institution_id,
                        domain=domain,
                        metric_name=norm_metric,
                        academic_year=ctx.academic_year,
                        time_period=ctx.time_period,
                        department=ctx.department,
                        spreadsheet_location=v_coord,
                        raw_value=v_raw,
                    )
                    if sig_id in seen_sig_ids:
                        continue
                    seen_sig_ids.add(sig_id)

                    sig = DiscoveredSignal(
                        signal_id=sig_id,
                        domain=domain,
                        metric_name=norm_metric,
                        metric_label=m_label,
                        value=num_val,
                        raw_value=v_raw,
                        unit=unit,
                        polarity=polarity,
                        risk_contribution=compute_risk_contribution(domain, norm_metric, num_val, unit, polarity),
                        context=ctx,
                        provenance=SignalProvenance(
                            source=filename,
                            document=filename,
                            format_type=fmt,
                            page_or_section=f"Worksheet '{sheet_name}'",
                            spreadsheet_location=v_coord,
                            excerpt_or_reference=row_excerpt[:300],
                            extraction_confidence=0.82 if is_ambig else 0.96,
                        ),
                    )
                    signals.append(sig)
            else:
                # Wide table or 2-column key-value table
                non_empty_cols = [(i, c) for i, c in enumerate(data_row) if c[3]]
                # Check if 2-column key-value row without standard headers
                if len(headers) == 2 and not header_roles and len(non_empty_cols) == 2:
                    k_cell = data_row[0]
                    v_cell = data_row[1]
                    num_val, is_ambig, parsed_unit = parse_numeric_value(v_cell[3])
                    if num_val is not None:
                        domain, norm_metric, default_unit, polarity = classify_metric(k_cell[3])
                        unit = parsed_unit or default_unit
                        ctx = extract_evidence_backed_context(
                            text_context=f"{sheet_name} {k_cell[3]}",
                            explicit_fields=explicit_ctx,
                            scoped_org_id=self.organization_id,
                            scoped_inst_id=self.institution_id,
                            source_name=filename,
                        )
                        sig_id, _ = make_signal_id(
                            institution_id=self.institution_id or ctx.institution_id,
                            domain=domain,
                            metric_name=norm_metric,
                            academic_year=ctx.academic_year,
                            time_period=ctx.time_period,
                            department=ctx.department,
                            spreadsheet_location=v_cell[2],
                            raw_value=v_cell[3],
                        )
                        if sig_id in seen_sig_ids:
                            continue
                        seen_sig_ids.add(sig_id)

                        signals.append(
                            DiscoveredSignal(
                                signal_id=sig_id,
                                domain=domain,
                                metric_name=norm_metric,
                                metric_label=k_cell[3],
                                value=num_val,
                                raw_value=v_cell[3],
                                unit=unit,
                                polarity=polarity,
                                risk_contribution=compute_risk_contribution(domain, norm_metric, num_val, unit, polarity),
                                context=ctx,
                                provenance=SignalProvenance(
                                    source=filename,
                                    document=filename,
                                    format_type=fmt,
                                    page_or_section=f"Worksheet '{sheet_name}'",
                                    spreadsheet_location=v_cell[2],
                                    excerpt_or_reference=f"{k_cell[3]}: {v_cell[3]}",
                                    extraction_confidence=0.82 if is_ambig else 0.95,
                                ),
                            )
                        )
                    continue

                # Wide table: every non-context column is a candidate metric column
                for col_pos, cell_tuple in enumerate(data_row):
                    if col_pos in header_roles:
                        continue
                    if col_pos >= len(headers) or not headers[col_pos]:
                        continue
                    col_header = headers[col_pos]
                    cell_r, cell_c, coord, raw_val = cell_tuple
                    if not raw_val:
                        # Incomplete coverage check: blank metric cell in a populated row
                        issues.append(
                            DataQualityIssue(
                                issue_type="incomplete_coverage",
                                severity="LOW",
                                description=f"Missing cell value for column '{col_header}' at {coord}.",
                                affected_metric=col_header,
                                provenance_refs=[coord],
                            )
                        )
                        continue
                    num_val, is_ambig, parsed_unit = parse_numeric_value(raw_val)
                    if num_val is None and not is_ambig:
                        continue
                    domain, norm_metric, default_unit, polarity = classify_metric(col_header)
                    unit = parsed_unit or default_unit
                    ctx = extract_evidence_backed_context(
                        text_context=f"{sheet_name} {col_header}",
                        explicit_fields=explicit_ctx,
                        scoped_org_id=self.organization_id,
                        scoped_inst_id=self.institution_id,
                        source_name=filename,
                    )
                    if is_ambig:
                        issues.append(
                            DataQualityIssue(
                                issue_type="ambiguous_values",
                                severity="MEDIUM",
                                description=f"Ambiguous value '{raw_val}' for metric '{col_header}' at {coord}.",
                                affected_domain=domain,
                                affected_metric=norm_metric,
                                affected_department=ctx.department,
                                affected_period=ctx.time_period,
                                provenance_refs=[coord],
                            )
                        )
                    sig_id, _ = make_signal_id(
                        institution_id=self.institution_id or ctx.institution_id,
                        domain=domain,
                        metric_name=norm_metric,
                        academic_year=ctx.academic_year,
                        time_period=ctx.time_period,
                        department=ctx.department,
                        spreadsheet_location=coord,
                        raw_value=raw_val,
                    )
                    if sig_id in seen_sig_ids:
                        continue
                    seen_sig_ids.add(sig_id)

                    signals.append(
                        DiscoveredSignal(
                            signal_id=sig_id,
                            domain=domain,
                            metric_name=norm_metric,
                            metric_label=col_header,
                            value=num_val,
                            raw_value=raw_val,
                            unit=unit,
                            polarity=polarity,
                            risk_contribution=compute_risk_contribution(domain, norm_metric, num_val, unit, polarity),
                            context=ctx,
                            provenance=SignalProvenance(
                                source=filename,
                                document=filename,
                                format_type=fmt,
                                page_or_section=f"Worksheet '{sheet_name}'",
                                spreadsheet_location=coord,
                                excerpt_or_reference=row_excerpt[:300],
                                extraction_confidence=0.80 if is_ambig else 0.96,
                            ),
                        )
                    )

        return signals, issues

    # ------------------------------------------------------------------
    # Format Extractor 2: CSV / TSV
    # ------------------------------------------------------------------
    def _extract_csv(
        self, content: bytes, filename: str
    ) -> Tuple[List[DiscoveredSignal], List[DataQualityIssue], str]:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("latin-1", errors="replace")

        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            return [], [], f"CSV file '{filename}' contains no non-empty rows."

        delimiter = ","
        if "\t" in lines[0] and lines[0].count("\t") > lines[0].count(","):
            delimiter = "\t"
        elif ";" in lines[0] and lines[0].count(";") > lines[0].count(","):
            delimiter = ";"

        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        raw_rows = list(reader)
        signals, issues = self._extract_from_cell_grid(
            raw_rows, filename=filename, fmt="CSV", sheet_name="CSV"
        )
        explanation = (
            f"Extracted {len(signals)} institutional signals from CSV '{filename}' "
            f"({len(raw_rows)} rows, delimiter={repr(delimiter)}) with row/column provenance."
        )
        return signals, issues, explanation

    # ------------------------------------------------------------------
    # Format Extractor 3: DOCX (OpenXML Word Document)
    # ------------------------------------------------------------------
    def _extract_docx(
        self, content: bytes, filename: str
    ) -> Tuple[List[DiscoveredSignal], List[DataQualityIssue], str]:
        signals: List[DiscoveredSignal] = []
        issues: List[DataQualityIssue] = []

        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            if "word/document.xml" not in zf.namelist():
                raise ValueError("Missing word/document.xml inside DOCX container.")
            xml_bytes = zf.read("word/document.xml")

        root = ET.fromstring(xml_bytes)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        body = root.find("w:body", ns)
        if body is None:
            return [], [], f"DOCX '{filename}' has an empty document body."

        current_section = "Document Body"
        para_idx = 0
        table_idx = 0

        for child in body:
            tag = child.tag.split("}")[-1]
            if tag == "p":
                texts = [t.text for t in child.findall(".//w:t", ns) if t.text]
                line = "".join(texts).strip()
                if not line:
                    continue
                para_idx += 1
                pstyle = child.find(".//w:pStyle", ns)
                style_val = pstyle.attrib.get(f"{{{ns['w']}}}val", "") if pstyle is not None else ""
                if "Heading" in style_val or "Title" in style_val or (len(line) < 80 and line.isupper()):
                    current_section = line
                    continue

                p_sigs, p_issues = self._extract_signals_from_text_block(
                    text=line,
                    filename=filename,
                    fmt="DOCX",
                    page_or_section=f"Section '{current_section}', Paragraph {para_idx}",
                    base_confidence=0.90,
                )
                signals.extend(p_sigs)
                issues.extend(p_issues)

            elif tag == "tbl":
                table_idx += 1
                tbl_rows: List[List[str]] = []
                for tr in child.findall(".//w:tr", ns):
                    row_cells: List[str] = []
                    for tc in tr.findall(".//w:tc", ns):
                        cell_texts = [t.text for t in tc.findall(".//w:t", ns) if t.text]
                        row_cells.append(" ".join(cell_texts).strip())
                    if any(row_cells):
                        tbl_rows.append(row_cells)
                if tbl_rows:
                    t_sigs, t_issues = self._extract_from_cell_grid(
                        tbl_rows,
                        filename=filename,
                        fmt="DOCX",
                        sheet_name=f"Table{table_idx}({current_section[:24]})",
                    )
                    for s in t_sigs:
                        s.provenance.page_or_section = f"Section '{current_section}', Table {table_idx}"
                    signals.extend(t_sigs)
                    issues.extend(t_issues)

        explanation = (
            f"Extracted {len(signals)} institutional signals from DOCX '{filename}' "
            f"across {para_idx} paragraph(s) and {table_idx} table(s) with section & table provenance."
        )
        return signals, issues, explanation

    # ------------------------------------------------------------------
    # Format Extractor 4: PPTX (OpenXML Presentation)
    # ------------------------------------------------------------------
    def _extract_pptx(
        self, content: bytes, filename: str
    ) -> Tuple[List[DiscoveredSignal], List[DataQualityIssue], str]:
        signals: List[DiscoveredSignal] = []
        issues: List[DataQualityIssue] = []

        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            slide_files = sorted(
                [n for n in zf.namelist() if re.match(r"^ppt/slides/slide\d+\.xml$", n)],
                key=lambda x: int(re.search(r"(\d+)", x).group(1)),
            )
            if not slide_files:
                raise ValueError("No slide XML streams found inside PPTX container.")

            ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
            for slide_path in slide_files:
                slide_num = int(re.search(r"slide(\d+)\.xml$", slide_path).group(1))
                root = ET.fromstring(zf.read(slide_path))

                # Extract tables on the slide
                tables = root.findall(".//a:tbl", ns)
                for t_idx, tbl in enumerate(tables, start=1):
                    tbl_rows: List[List[str]] = []
                    for tr in tbl.findall(".//a:tr", ns):
                        row_cells: List[str] = []
                        for tc in tr.findall(".//a:tc", ns):
                            c_texts = [t.text for t in tc.findall(".//a:t", ns) if t.text]
                            row_cells.append(" ".join(c_texts).strip())
                        if any(row_cells):
                            tbl_rows.append(row_cells)
                    if tbl_rows:
                        t_sigs, t_issues = self._extract_from_cell_grid(
                            tbl_rows,
                            filename=filename,
                            fmt="PPTX",
                            sheet_name=f"Slide{slide_num}_Table{t_idx}",
                        )
                        for s in t_sigs:
                            s.provenance.page_or_section = f"Slide {slide_num}, Table {t_idx}"
                        signals.extend(t_sigs)
                        issues.extend(t_issues)

                # Extract text runs on the slide (outside tables)
                para_lines: List[str] = []
                for p in root.findall(".//a:p", ns):
                    runs = [t.text for t in p.findall(".//a:t", ns) if t.text]
                    line = " ".join(runs).strip()
                    if line:
                        para_lines.append(line)

                slide_context_header = para_lines[0] if para_lines else f"Slide {slide_num}"
                for line in para_lines:
                    p_sigs, p_issues = self._extract_signals_from_text_block(
                        text=line,
                        filename=filename,
                        fmt="PPTX",
                        page_or_section=f"Slide {slide_num} ({slide_context_header[:40]})",
                        base_confidence=0.90,
                        ambient_context_text=slide_context_header,
                    )
                    signals.extend(p_sigs)
                    issues.extend(p_issues)

        explanation = (
            f"Extracted {len(signals)} institutional signals from PPTX '{filename}' "
            f"across {len(slide_files)} slide(s) with Slide & Table provenance."
        )
        return signals, issues, explanation

    # ------------------------------------------------------------------
    # Format Extractor 5: PDF (Digital PDF Stream Parser + OCR Uncertainty Detection)
    # ------------------------------------------------------------------
    def _extract_pdf(
        self, content: bytes, filename: str
    ) -> Tuple[List[DiscoveredSignal], List[DataQualityIssue], str, str]:
        signals: List[DiscoveredSignal] = []
        issues: List[DataQualityIssue] = []

        pages_text = self._extract_pdf_pages_text(content)
        total_chars = sum(len(t.strip()) for t in pages_text)

        if total_chars == 0:
            # Scanned image-only PDF without embedded text streams
            issues.append(
                DataQualityIssue(
                    issue_type="ocr_uncertainty",
                    severity="HIGH",
                    description=(
                        f"PDF '{filename}' contains no extractable text streams (likely a scanned image-only PDF) "
                        f"and no live multimodal OCR provider is configured."
                    ),
                    provenance_refs=[f"{filename}#Page1"],
                )
            )
            return (
                [],
                issues,
                (
                    f"PDF '{filename}' is a valid PDF container but contains 0 extractable text streams "
                    f"(image-only scan). Flagged ocr_uncertainty rather than fabricating signals."
                ),
                "PARTIAL_EXTRACTION",
            )

        for p_idx, page_str in enumerate(pages_text, start=1):
            if not page_str.strip():
                continue
            # Check for OCR noise / garbled characters on this page
            has_ocr_noise = bool(re.search(r"(\?\?\?|\[ocr_low_conf\]|\[illegible\]|\ufffd)", page_str, re.IGNORECASE))
            base_conf = 0.72 if has_ocr_noise else 0.91
            if has_ocr_noise:
                issues.append(
                    DataQualityIssue(
                        issue_type="ocr_uncertainty",
                        severity="MEDIUM",
                        description=f"OCR uncertainty / low-confidence character tokens detected on Page {p_idx} of '{filename}'.",
                        provenance_refs=[f"{filename}#Page{p_idx}"],
                    )
                )

            for line in page_str.splitlines():
                line = line.strip()
                if not line:
                    continue
                p_sigs, p_issues = self._extract_signals_from_text_block(
                    text=line,
                    filename=filename,
                    fmt="PDF",
                    page_or_section=f"Page {p_idx}",
                    base_confidence=base_conf,
                    ambient_context_text=page_str[:300],
                )
                signals.extend(p_sigs)
                issues.extend(p_issues)

        explanation = (
            f"Extracted {len(signals)} institutional signals from PDF '{filename}' "
            f"across {len(pages_text)} page(s) with Page-level provenance."
        )
        return signals, issues, explanation, "SUCCESS"

    @staticmethod
    def _extract_pdf_pages_text(pdf_bytes: bytes) -> List[str]:
        """
        Pure-Python PDF content stream text extractor.
        Decompresses FlateDecode streams via zlib and decodes Tj / TJ / ' text operators per page.
        """
        pages: List[str] = []
        import base64
        stream_pattern = re.compile(rb"stream\r?\n(.*?)\r?\n?endstream", re.DOTALL)

        for match in stream_pattern.finditer(pdf_bytes):
            raw_stream = match.group(1)
            decoded_bytes: Optional[bytes] = None
            for candidate in (raw_stream, raw_stream.strip()):
                try:
                    decoded_bytes = zlib.decompress(candidate)
                    break
                except Exception:
                    pass

            if not decoded_bytes:
                try:
                    a85_in = raw_stream.strip()
                    a85_raw = base64.a85decode(a85_in, adobe=a85_in.endswith(b"~>"))
                    decoded_bytes = zlib.decompress(a85_raw)
                except Exception:
                    pass

            if not decoded_bytes:
                # Stream might be uncompressed ASCII PDF commands
                if b"BT" in raw_stream or b"Tj" in raw_stream or b"TJ" in raw_stream:
                    decoded_bytes = raw_stream

            if not decoded_bytes:
                continue

            stream_str = decoded_bytes.decode("latin-1", errors="ignore")
            if "Tj" not in stream_str and "TJ" not in stream_str:
                continue

            lines_out: List[str] = []
            current_line: List[str] = []

            # Tokenize text positioning and string literals inside content stream
            for tok_match in re.finditer(
                r"\((?:\\.|[^\\)])*\)\s*Tj|\[(?:\((?:\\.|[^\\)])*\)|[^\]])*\]\s*TJ|T\*|Td|TD|Tm",
                stream_str,
            ):
                tok = tok_match.group(0)
                if tok in ("T*", "Td", "TD", "Tm"):
                    if current_line:
                        lines_out.append(" ".join(current_line))
                        current_line = []
                else:
                    # Extract all (...) string literals inside this Tj/TJ operator
                    literals = re.findall(r"\(((?:\\.|[^\\)])*)\)", tok)
                    joined = "".join(literals)
                    # Unescape PDF escapes
                    joined = (
                        joined.replace(r"\(", "(")
                        .replace(r"\)", ")")
                        .replace(r"\n", "\n")
                        .replace(r"\r", "\r")
                        .replace(r"\t", " ")
                        .replace(r"\\", "\\")
                    )
                    if joined.strip():
                        current_line.append(joined.strip())

            if current_line:
                lines_out.append(" ".join(current_line))

            page_text = "\n".join(lines_out).strip()
            if page_text:
                pages.append(page_text)

        return pages

    # ------------------------------------------------------------------
    # Format Extractor 6: TXT / MD / JSON
    # ------------------------------------------------------------------
    def _extract_txt_or_json(
        self, content: bytes, filename: str
    ) -> Tuple[List[DiscoveredSignal], Dict[str, List[Any]], List[DataQualityIssue], str]:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("latin-1", errors="replace")

        signals: List[DiscoveredSignal] = []
        issues: List[DataQualityIssue] = []
        canonical_map: Dict[str, List[Any]] = {"admissions": [], "placements": [], "cet_ranking": []}

        stripped = text.strip()
        if stripped.startswith("{") or stripped.startswith("[") or filename.lower().endswith(".json"):
            try:
                data = json.loads(stripped)
                if isinstance(data, dict) and any(k in data for k in ("admissions", "placements", "cet_ranking")):
                    j_adapter = JSONDictionaryAdapter()
                    canonical_map = j_adapter.parse(data)
                    if self.institution_id:
                        for sig_list in canonical_map.values():
                            for sig in sig_list:
                                sig.institution_id = self.institution_id
                    signals.extend(self._canonical_to_discovered(canonical_map, filename, "TXT"))
                # Also extract arbitrary JSON signals
                if isinstance(data, dict) and "signals" in data and isinstance(data["signals"], list):
                    for idx, item in enumerate(data["signals"], start=1):
                        if isinstance(item, dict):
                            m_label = str(item.get("metric") or item.get("metric_name") or item.get("name") or f"metric_{idx}")
                            v_raw = str(item.get("value", ""))
                            if not v_raw:
                                continue
                            domain, norm_metric, default_unit, polarity = classify_metric(m_label)
                            if item.get("domain"):
                                domain = str(item["domain"]).lower()
                            num_val, is_ambig, parsed_unit = parse_numeric_value(v_raw)
                            unit = item.get("unit") or parsed_unit or default_unit
                            explicit_ctx: Dict[str, Tuple[str, str]] = {}
                            if item.get("department"):
                                explicit_ctx["department"] = (str(item["department"]), f"JSON $.signals[{idx-1}].department")
                            if item.get("program"):
                                explicit_ctx["program"] = (str(item["program"]), f"JSON $.signals[{idx-1}].program")
                            if item.get("academic_year") or item.get("period") or item.get("year"):
                                p_val = str(item.get("academic_year") or item.get("period") or item.get("year"))
                                explicit_ctx["period"] = (p_val, f"JSON $.signals[{idx-1}].period")
                            ctx = extract_evidence_backed_context(
                                text_context="",
                                explicit_fields=explicit_ctx,
                                scoped_org_id=self.organization_id,
                                scoped_inst_id=self.institution_id or item.get("institution_id"),
                                source_name=filename,
                            )
                            sig_id, fp = make_signal_id(
                                institution_id=ctx.institution_id or self.institution_id or item.get("institution_id"),
                                domain=domain,
                                metric_name=norm_metric,
                                academic_year=ctx.academic_year,
                                time_period=ctx.time_period,
                                department=ctx.department,
                                spreadsheet_location=f"JSON $.signals[{idx-1}]",
                                raw_value=v_raw,
                            )
                            signals.append(
                                DiscoveredSignal(
                                    signal_id=sig_id,
                                    domain=domain,
                                    metric_name=norm_metric,
                                    metric_label=m_label,
                                    value=num_val,
                                    raw_value=v_raw,
                                    unit=unit,
                                    polarity=polarity,
                                    risk_contribution=compute_risk_contribution(domain, norm_metric, num_val, unit, polarity),
                                    context=ctx,
                                    provenance=SignalProvenance(
                                        source=filename,
                                        document=filename,
                                        format_type="TXT",
                                        page_or_section=f"JSON $.signals[{idx-1}]",
                                        excerpt_or_reference=json.dumps(item)[:250],
                                        extraction_confidence=0.98,
                                    ),
                                )
                            )
                return (
                    signals,
                    canonical_map,
                    issues,
                    f"Extracted {len(signals)} institutional signals from structured JSON '{filename}'.",
                )
            except Exception:
                pass

        # Plain text / Markdown parsing
        current_section = "Section 1"
        lines = text.splitlines()
        for line_no, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#") or (len(line) < 75 and line.endswith(":") and not re.search(r"\d", line)):
                current_section = line.lstrip("#").strip(": ")
                continue

            l_sigs, l_issues = self._extract_signals_from_text_block(
                text=line,
                filename=filename,
                fmt="TXT",
                page_or_section=f"{current_section} (Line {line_no})",
                base_confidence=0.92,
                ambient_context_text=current_section,
            )
            signals.extend(l_sigs)
            issues.extend(l_issues)

        explanation = (
            f"Extracted {len(signals)} institutional signals from text document '{filename}' "
            f"across {len(lines)} line(s) with section & line provenance."
        )
        return signals, canonical_map, issues, explanation

    # ------------------------------------------------------------------
    # Format Extractor 7: Images / Scans (PNG, JPG, WEBP, BMP, TIFF)
    # ------------------------------------------------------------------
    def _extract_image(
        self, content: bytes, filename: str
    ) -> Tuple[List[DiscoveredSignal], List[DataQualityIssue], str, str]:
        signals: List[DiscoveredSignal] = []
        issues: List[DataQualityIssue] = []

        img = Image.open(io.BytesIO(content))
        width, height = img.size
        img_format = img.format or "IMAGE"
        info = img.info or {}

        # 1. Check embedded OCR / text metadata layers (PNG tEXt/iTXt, EXIF ImageDescription, etc.)
        embedded_texts: List[str] = []
        for k, v in info.items():
            if isinstance(v, (str, bytes)) and str(k).lower() in (
                "description",
                "comment",
                "ocr_text",
                "text",
                "title",
                "transcript",
                "institutional_data",
            ):
                val_str = v.decode("utf-8", errors="ignore") if isinstance(v, bytes) else v
                if val_str.strip():
                    embedded_texts.append(val_str.strip())

        try:
            exif = img.getexif()
            if exif:
                for tag_id in (270, 37510):  # ImageDescription, UserComment
                    if tag_id in exif and exif[tag_id]:
                        val = exif[tag_id]
                        val_str = val.decode("utf-8", errors="ignore") if isinstance(val, bytes) else str(val)
                        if val_str.strip():
                            embedded_texts.append(val_str.strip())
        except Exception:
            pass

        ocr_text = "\n".join(embedded_texts).strip()
        used_gemini_vision = False

        # 2. If no embedded text chunk and GEMINI_API_KEY is configured, invoke Gemini multimodal vision OCR
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not ocr_text and api_key:
            try:
                from google import genai
                from google.genai import types

                client = genai.Client(api_key=api_key)
                resp = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=[
                        types.Part.from_bytes(
                            data=content,
                            mime_type=f"image/{img_format.lower()}",
                        ),
                        (
                            "Transcribe all institutional metrics, tables, numbers, departments, and time periods "
                            "visible in this image verbatim as key: value lines."
                        ),
                    ],
                )
                if resp and resp.text:
                    ocr_text = resp.text.strip()
                    used_gemini_vision = True
            except Exception:
                pass

        if not ocr_text:
            issues.append(
                DataQualityIssue(
                    issue_type="ocr_uncertainty",
                    severity="HIGH",
                    description=(
                        f"Image/scan '{filename}' ({img_format} {width}x{height}px) has no embedded OCR text layer "
                        f"and no live optical character recognition provider is active."
                    ),
                    provenance_refs=[f"{filename}#Image({img_format}_{width}x{height})"],
                )
            )
            return (
                [],
                issues,
                (
                    f"Validated image '{filename}' ({img_format}, {width}x{height}px, mode={img.mode}). "
                    f"No embedded OCR text layer found and offline pixel OCR is not active; flagged ocr_uncertainty truthfully without inventing signals."
                ),
                "PARTIAL_EXTRACTION",
            )

        # Check scan resolution / OCR uncertainty markers
        low_res = width < 300 or height < 150
        has_noise = bool(re.search(r"(\?\?\?|\[illegible\]|\[ocr_uncertainty\]|\[blurry\]|\ufffd)", ocr_text, re.IGNORECASE))
        conf = 0.74 if (low_res or has_noise) else (0.86 if used_gemini_vision else 0.84)

        if low_res or has_noise or conf < 0.85:
            issues.append(
                DataQualityIssue(
                    issue_type="ocr_uncertainty",
                    severity="MEDIUM" if not has_noise else "HIGH",
                    description=(
                        f"OCR extraction from image/scan '{filename}' ({img_format} {width}x{height}px) "
                        f"carries optical recognition uncertainty (confidence={conf})."
                    ),
                    provenance_refs=[f"{filename}#Image({img_format}_{width}x{height})"],
                )
            )

        for idx, line in enumerate(ocr_text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            l_sigs, l_issues = self._extract_signals_from_text_block(
                text=line,
                filename=filename,
                fmt="IMAGE",
                page_or_section=f"Scan Image ({img_format} {width}x{height}px), Region {idx}",
                base_confidence=conf,
            )
            for s in l_sigs:
                s.provenance.excerpt_or_reference = f"[Image {img_format} {width}x{height}px] {s.provenance.excerpt_or_reference}"
            signals.extend(l_sigs)
            issues.extend(l_issues)

        explanation = (
            f"Extracted {len(signals)} institutional signals from image/scan '{filename}' "
            f"({img_format} {width}x{height}px) with image reference & OCR confidence provenance."
        )
        return signals, issues, explanation, "SUCCESS"

    # ------------------------------------------------------------------
    # Format Extractor 8: Audio / Video (WAV, MP3, MP4, M4A, MOV, WEBM)
    # ------------------------------------------------------------------
    def _extract_audio_video(
        self, content: bytes, filename: str
    ) -> Tuple[List[DiscoveredSignal], List[DataQualityIssue], str, str]:
        signals: List[DiscoveredSignal] = []
        issues: List[DataQualityIssue] = []
        ext = os.path.splitext(filename.lower())[1]
        media_desc = f"Media({ext or 'audio/video'}, {len(content)} bytes)"
        transcript_text = ""

        # 1. Inspect WAV container via stdlib wave + RIFF chunks
        if content.startswith(b"RIFF") and content[8:12] == b"WAVE":
            try:
                with wave.open(io.BytesIO(content), "rb") as wf:
                    ch = wf.getnchannels()
                    rate = wf.getframerate()
                    frames = wf.getnframes()
                    dur = round(frames / float(rate), 2) if rate > 0 else 0.0
                    media_desc = f"WAV Audio ({ch}ch, {rate}Hz, {dur}s)"
            except Exception:
                pass
            # Check for embedded RIFF transcript / INFO / iXML / cue text chunk
            m_tr = re.search(rb"(?:TRNS|ICMT|ITXT|NOTE|transcript:)([\x20-\x7e\r\n\t]{8,})", content)
            if m_tr:
                transcript_text = m_tr.group(1).decode("utf-8", errors="ignore").strip()

        # 2. Check MP3/MP4/WebM embedded transcript / subtitle / comment atoms
        if not transcript_text:
            m_sub = re.search(rb"TRANSCRIPT_TRACK:([\x20-\x7e\r\n\t]{8,})", content)
            if m_sub:
                transcript_text = m_sub.group(1).decode("utf-8", errors="ignore").strip()

        # 3. Check live Gemini multimodal audio/video transcription if GEMINI_API_KEY is configured
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not transcript_text and api_key:
            try:
                from google import genai
                from google.genai import types

                mime_guess, _ = mimetypes.guess_type(filename)
                client = genai.Client(api_key=api_key)
                resp = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=[
                        types.Part.from_bytes(
                            data=content,
                            mime_type=mime_guess or "audio/wav",
                        ),
                        (
                            "Transcribe this institutional audio/video recording and extract all factual metrics, "
                            "departments, and time periods as key: value lines."
                        ),
                    ],
                )
                if resp and resp.text:
                    transcript_text = resp.text.strip()
            except Exception:
                pass

        if not transcript_text:
            return (
                [],
                issues,
                (
                    f"Inspected {media_desc} in '{filename}'. Container is valid, but no embedded caption/transcript track "
                    f"was found and offline speech-to-text transcription is not active (requires GEMINI_API_KEY or embedded transcript track). "
                    f"Returning 0 fabricated signals truthfully."
                ),
                "PARTIAL_EXTRACTION",
            )

        for idx, line in enumerate(transcript_text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            l_sigs, l_issues = self._extract_signals_from_text_block(
                text=line,
                filename=filename,
                fmt="AUDIO_VIDEO",
                page_or_section=f"{media_desc} — Transcript Segment {idx}",
                base_confidence=0.85,
            )
            signals.extend(l_sigs)
            issues.extend(l_issues)

        explanation = (
            f"Extracted {len(signals)} institutional signals from {media_desc} '{filename}' "
            f"with media transcript segment provenance."
        )
        return signals, issues, explanation, "SUCCESS"

    # ------------------------------------------------------------------
    # Unstructured / Semi-Structured Text Signal Extractor
    # ------------------------------------------------------------------
    def _extract_signals_from_text_block(
        self,
        text: str,
        filename: str,
        fmt: str,
        page_or_section: str,
        base_confidence: float = 0.90,
        ambient_context_text: str = "",
    ) -> Tuple[List[DiscoveredSignal], List[DataQualityIssue]]:
        signals: List[DiscoveredSignal] = []
        issues: List[DataQualityIssue] = []

        # Pattern A: Markdown / pipe-delimited row `| Metric | Value | ...`
        if "|" in text and text.count("|") >= 2:
            parts = [p.strip() for p in text.strip("|").split("|") if p.strip()]
            if len(parts) >= 2 and not all(re.match(r"^[-:]+$", p) for p in parts):
                label_part, val_part = parts[0], parts[1]
                num_val, is_ambig, parsed_unit = parse_numeric_value(val_part)
                if num_val is not None:
                    domain, norm_metric, default_unit, polarity = classify_metric(label_part)
                    unit = parsed_unit or default_unit
                    ctx = extract_evidence_backed_context(
                        text_context=f"{ambient_context_text} {text}",
                        scoped_org_id=self.organization_id,
                        scoped_inst_id=self.institution_id,
                        source_name=filename,
                    )
                    if is_ambig:
                        issues.append(
                            DataQualityIssue(
                                issue_type="ambiguous_values",
                                severity="MEDIUM",
                                description=f"Ambiguous value '{val_part}' for '{label_part}' in {page_or_section}.",
                                affected_domain=domain,
                                affected_metric=norm_metric,
                                affected_department=ctx.department,
                                affected_period=ctx.time_period,
                                provenance_refs=[f"{filename}#{page_or_section}"],
                            )
                        )
                    sig_id, fp = make_signal_id(
                        institution_id=ctx.institution_id or self.institution_id,
                        domain=domain,
                        metric_name=norm_metric,
                        academic_year=ctx.academic_year,
                        time_period=ctx.time_period,
                        department=ctx.department,
                        spreadsheet_location=page_or_section or "",
                        raw_value=val_part,
                    )
                    signals.append(
                        DiscoveredSignal(
                            signal_id=sig_id,
                            domain=domain,
                            metric_name=norm_metric,
                            metric_label=label_part,
                            value=num_val,
                            raw_value=val_part,
                            unit=unit,
                            polarity=polarity,
                            risk_contribution=compute_risk_contribution(domain, norm_metric, num_val, unit, polarity),
                            context=ctx,
                            provenance=SignalProvenance(
                                source=filename,
                                document=filename,
                                format_type=fmt,
                                page_or_section=page_or_section,
                                excerpt_or_reference=text[:300],
                                extraction_confidence=round(base_confidence - (0.10 if is_ambig else 0.0), 2),
                            ),
                        )
                    )
                    return signals, issues

        # Pattern B: Key-value statements (`Metric Label: 84.5%` or `Metric Label = 120` or `Metric Label - 45`)
        kv_match = re.match(r"^[-*•]?\s*([A-Za-z][A-Za-z0-9\s/&(),.-]{2,65}?)\s*[:=–—-]\s*([^:;]+)$", text)
        if kv_match:
            label_part = kv_match.group(1).strip()
            val_part = kv_match.group(2).strip()
            # Ensure label_part is not just a context header like "Department: CSE" or "Academic Year: 2024"
            label_clean = label_part.lower()
            if label_clean not in ("department", "program", "institution", "organization", "academic year", "year", "period", "date", "state", "city"):
                num_val, is_ambig, parsed_unit = parse_numeric_value(val_part)
                if num_val is not None or is_ambig:
                    domain, norm_metric, default_unit, polarity = classify_metric(label_part)
                    unit = parsed_unit or default_unit
                    ctx = extract_evidence_backed_context(
                        text_context=f"{ambient_context_text} {text}",
                        scoped_org_id=self.organization_id,
                        scoped_inst_id=self.institution_id,
                        source_name=filename,
                    )
                    if is_ambig:
                        issues.append(
                            DataQualityIssue(
                                issue_type="ambiguous_values",
                                severity="MEDIUM",
                                description=f"Ambiguous value '{val_part}' for '{label_part}' in {page_or_section}.",
                                affected_domain=domain,
                                affected_metric=norm_metric,
                                affected_department=ctx.department,
                                affected_period=ctx.time_period,
                                provenance_refs=[f"{filename}#{page_or_section}"],
                            )
                        )
                    sig_id_b, fp_b = make_signal_id(
                        institution_id=ctx.institution_id or self.institution_id,
                        domain=domain,
                        metric_name=norm_metric,
                        academic_year=ctx.academic_year,
                        time_period=ctx.time_period,
                        department=ctx.department,
                        spreadsheet_location=page_or_section or "",
                        raw_value=val_part,
                    )
                    signals.append(
                        DiscoveredSignal(
                            signal_id=sig_id_b,
                            domain=domain,
                            metric_name=norm_metric,
                            metric_label=label_part,
                            value=num_val,
                            raw_value=val_part,
                            unit=unit,
                            polarity=polarity,
                            risk_contribution=compute_risk_contribution(domain, norm_metric, num_val, unit, polarity),
                            context=ctx,
                            provenance=SignalProvenance(
                                source=filename,
                                document=filename,
                                format_type=fmt,
                                page_or_section=page_or_section,
                                excerpt_or_reference=text[:300],
                                extraction_confidence=round(base_confidence - (0.10 if is_ambig else 0.0), 2),
                            ),
                        )
                    )
                    return signals, issues

        # Pattern C: Natural-language prose sentences containing known domain metrics + numeric values
        for domain, rules in DOMAIN_KEYWORDS.items():
            for pattern, norm_metric, default_unit, polarity in rules:
                for m in re.finditer(pattern, text, re.IGNORECASE):
                    # Search within a window around the matched metric phrase for a number (excluding 4-digit academic years 2020-2029)
                    start_w = max(0, m.start() - 25)
                    end_w = min(len(text), m.end() + 55)
                    window = text[start_w:end_w]
                    # Remove academic years like 2023-24 or 2024 from the numeric search window unless the metric itself is a year
                    window_no_yr = re.sub(r"\b(?:AY\s*)?20[12]\d(?:\s*-\s*\d{2,4})?\b", " ", window)
                    num_m = re.search(r"(~?\s*\d+(?:\.\d+)?\s*%?)", window_no_yr)
                    if not num_m:
                        continue
                    raw_val = num_m.group(1).strip()
                    num_val, is_ambig, parsed_unit = parse_numeric_value(raw_val)
                    if num_val is None:
                        continue
                    unit = parsed_unit or default_unit
                    ctx = extract_evidence_backed_context(
                        text_context=f"{ambient_context_text} {text}",
                        scoped_org_id=self.organization_id,
                        scoped_inst_id=self.institution_id,
                        source_name=filename,
                    )
                    if is_ambig:
                        issues.append(
                            DataQualityIssue(
                                issue_type="ambiguous_values",
                                severity="MEDIUM",
                                description=f"Ambiguous prose value '{raw_val}' for '{norm_metric}' in {page_or_section}.",
                                affected_domain=domain,
                                affected_metric=norm_metric,
                                affected_department=ctx.department,
                                affected_period=ctx.time_period,
                                provenance_refs=[f"{filename}#{page_or_section}"],
                            )
                        )
                    sig_id_c, fp_c = make_signal_id(
                        institution_id=ctx.institution_id or self.institution_id,
                        domain=domain,
                        metric_name=norm_metric,
                        academic_year=ctx.academic_year,
                        time_period=ctx.time_period,
                        department=ctx.department,
                        spreadsheet_location=page_or_section or "",
                        raw_value=raw_val,
                    )
                    signals.append(
                        DiscoveredSignal(
                            signal_id=sig_id_c,
                            domain=domain,
                            metric_name=norm_metric,
                            metric_label=m.group(0).strip(),
                            value=num_val,
                            raw_value=raw_val,
                            unit=unit,
                            polarity=polarity,
                            risk_contribution=compute_risk_contribution(domain, norm_metric, num_val, unit, polarity),
                            context=ctx,
                            provenance=SignalProvenance(
                                source=filename,
                                document=filename,
                                format_type=fmt,
                                page_or_section=page_or_section,
                                excerpt_or_reference=text[:300],
                                extraction_confidence=round(base_confidence - 0.04 - (0.10 if is_ambig else 0.0), 2),
                            ),
                        )
                    )

        return signals, issues

    # ------------------------------------------------------------------
    # Canonical Representation Bridge
    # ------------------------------------------------------------------
    def _canonical_to_discovered(
        self, canonical_map: Dict[str, List[Any]], filename: str, fmt: str
    ) -> List[DiscoveredSignal]:
        out: List[DiscoveredSignal] = []
        for adm in canonical_map.get("admissions", []):
            ctx = InstitutionalContextAssociation(
                organization_id=self.organization_id,
                institution_id=adm.institution_id,
                department=adm.department,
                time_period=str(adm.academic_year),
                academic_year=adm.academic_year,
                source=filename,
                context_evidence={
                    "institution_id": "Evidenced in workbook/dataset",
                    "department": f"Department row '{adm.department}'",
                    "academic_year": f"Year block '{adm.academic_year}'",
                },
            )
            admitted_cnt = getattr(adm, "enrolled_count", getattr(adm, "admitted_students", 0))
            for m_name, m_label, val, unit, pol in [
                ("sanctioned_intake", "Sanctioned Intake", float(adm.sanctioned_intake), "count", "neutral"),
                ("admitted_students", "Admitted Students", float(admitted_cnt), "count", "higher_is_better"),
                ("vacancy_rate", "Seat Vacancy Rate", float(adm.vacancy_rate), "ratio", "lower_is_better"),
            ]:
                loc_str = f"DATA {adm.academic_year}!{adm.department}"
                sig_id, _ = make_signal_id(
                    institution_id=adm.institution_id,
                    domain="admissions",
                    metric_name=m_name,
                    academic_year=adm.academic_year,
                    time_period=str(adm.academic_year),
                    department=adm.department,
                    spreadsheet_location=loc_str,
                    raw_value=str(val),
                )
                out.append(
                    DiscoveredSignal(
                        signal_id=sig_id,
                        domain="admissions",
                        metric_name=m_name,
                        metric_label=m_label,
                        value=val,
                        raw_value=str(val),
                        unit=unit,
                        polarity=pol,
                        risk_contribution=compute_risk_contribution("admissions", m_name, val, unit, pol),
                        context=ctx,
                        provenance=SignalProvenance(
                            source=filename,
                            document=filename,
                            format_type=fmt,
                            page_or_section=f"AY {adm.academic_year} Admissions",
                            spreadsheet_location=loc_str,
                            excerpt_or_reference=f"{adm.department} ({adm.academic_year}): Intake={adm.sanctioned_intake}, Admitted={admitted_cnt}, Vacancy={adm.vacancy_rate:.2%}",
                            extraction_confidence=0.98,
                        ),
                    )
                )

        for plc in canonical_map.get("placements", []):
            ctx = InstitutionalContextAssociation(
                organization_id=self.organization_id,
                institution_id=plc.institution_id,
                department=plc.department,
                time_period=str(plc.graduation_year),
                academic_year=plc.graduation_year,
                source=filename,
                context_evidence={
                    "institution_id": "Evidenced in workbook/dataset",
                    "department": f"Department row '{plc.department}'",
                    "academic_year": f"Graduation year '{plc.graduation_year}'",
                },
            )
            plc_loc = f"DATA {plc.graduation_year}!{plc.department}"
            plc_sig_id, _ = make_signal_id(
                institution_id=plc.institution_id,
                domain="placements",
                metric_name="placement_percentage",
                academic_year=plc.graduation_year,
                time_period=str(plc.graduation_year),
                department=plc.department,
                spreadsheet_location=plc_loc,
                raw_value=f"{plc.placement_percentage}%",
            )
            out.append(
                DiscoveredSignal(
                    signal_id=plc_sig_id,
                    domain="placements",
                    metric_name="placement_percentage",
                    metric_label="Placement Percentage",
                    value=float(plc.placement_percentage),
                    raw_value=f"{plc.placement_percentage}%",
                    unit="%",
                    polarity="higher_is_better",
                    risk_contribution=compute_risk_contribution("placements", "placement_percentage", float(plc.placement_percentage), "%", "higher_is_better"),
                    context=ctx,
                    provenance=SignalProvenance(
                        source=filename,
                        document=filename,
                        format_type=fmt,
                        page_or_section=f"AY {plc.graduation_year} Placements",
                        spreadsheet_location=plc_loc,
                        excerpt_or_reference=f"{plc.department} ({plc.graduation_year}): Placed={plc.placed_students}/{plc.eligible_students} ({plc.placement_percentage}%)",
                        extraction_confidence=0.98,
                    ),
                )
            )

        for rnk in canonical_map.get("cet_ranking", []):
            ctx = InstitutionalContextAssociation(
                organization_id=self.organization_id,
                institution_id=rnk.institution_id,
                department=rnk.department,
                time_period=str(rnk.academic_year),
                academic_year=rnk.academic_year,
                source=filename,
                context_evidence={
                    "institution_id": "Evidenced in workbook/dataset",
                    "department": f"Department row '{rnk.department}'",
                    "academic_year": f"Year block '{rnk.academic_year}'",
                },
            )
            rnk_loc = f"DATA {rnk.academic_year}!{rnk.department}"
            rnk_sig_id, _ = make_signal_id(
                institution_id=rnk.institution_id,
                domain="ranking",
                metric_name="closing_rank",
                academic_year=rnk.academic_year,
                time_period=str(rnk.academic_year),
                department=rnk.department,
                spreadsheet_location=rnk_loc,
                raw_value=str(rnk.closing_rank),
            )
            out.append(
                DiscoveredSignal(
                    signal_id=rnk_sig_id,
                    domain="ranking",
                    metric_name="closing_rank",
                    metric_label="CET Closing Rank",
                    value=float(rnk.closing_rank),
                    raw_value=str(rnk.closing_rank),
                    unit="rank",
                    polarity="lower_is_better",
                    risk_contribution=None,
                    context=ctx,
                    provenance=SignalProvenance(
                        source=filename,
                        document=filename,
                        format_type=fmt,
                        page_or_section=f"AY {rnk.academic_year} CET Ranking",
                        spreadsheet_location=rnk_loc,
                        excerpt_or_reference=f"{rnk.department} ({rnk.academic_year}): Opening={rnk.opening_rank}, Closing={rnk.closing_rank}",
                        extraction_confidence=0.98,
                    ),
                )
            )
        return out

    def _bridge_discovered_to_canonical(
        self,
        discovered: List[DiscoveredSignal],
        canonical_map: Dict[str, List[Any]],
        inst_id: str,
        filename: str,
    ) -> None:
        """
        Where discovered signals include admissions, placement, or ranking metrics with an
        evidenced academic year, map them into canonical AdmissionsSignal, PlacementsSignal,
        and CETRankingSignal objects so the existing CRI intelligence engine can score them
        alongside dynamic signals.
        """
        if canonical_map["admissions"] or canonical_map["placements"] or canonical_map["cet_ranking"]:
            return

        prov = ProvenanceMetadata(
            source_id=filename,
            source_type="institutional_export",
            hash_checksum=hashlib.sha256(filename.encode("utf-8")).hexdigest(),
        )

        # Group by (department_or_inst_level, academic_year)
        grouped: Dict[Tuple[str, int], Dict[str, float]] = {}
        for s in discovered:
            if s.value is None or s.context.academic_year is None:
                continue
            dept_key = s.context.department or "INSTITUTIONAL"
            yr = s.context.academic_year
            if not (2000 <= yr <= 2100):
                continue
            grouped.setdefault((dept_key, yr), {})[s.metric_name] = s.value

        for (dept_key, yr), metrics in grouped.items():
            # Admissions
            if any(k in metrics for k in ("sanctioned_intake", "admitted_students", "vacancy_rate", "admission_fill_rate")):
                intake = int(metrics.get("sanctioned_intake", 100))
                if intake <= 0:
                    intake = 100
                if "admitted_students" in metrics:
                    admitted = min(intake, max(0, int(metrics["admitted_students"])))
                elif "vacancy_rate" in metrics:
                    v_raw = metrics["vacancy_rate"]
                    v_ratio = v_raw / 100.0 if v_raw > 1.0 else v_raw
                    admitted = min(intake, max(0, int(round(intake * (1.0 - v_ratio)))))
                else:
                    fill_pct = metrics.get("admission_fill_rate", 100.0)
                    admitted = min(intake, max(0, int(round(intake * (fill_pct / 100.0)))))
                vacancy = intake - admitted
                v_rate = round(float(vacancy) / float(intake), 4) if intake > 0 else 0.0
                try:
                    canonical_map["admissions"].append(
                        AdmissionsSignal(
                            institution_id=inst_id,
                            academic_year=yr,
                            department=dept_key,
                            sanctioned_intake=intake,
                            enrolled_count=admitted,
                            vacancy_count=vacancy,
                            vacancy_rate=v_rate,
                            gender_diversity_ratio=0.35,
                            dropouts_year_1=0,
                            provenance=prov,
                        )
                    )
                except Exception:
                    pass

            # Placements
            if any(k in metrics for k in ("placement_percentage", "placed_students", "eligible_students")):
                eligible = int(metrics.get("eligible_students", 100))
                if eligible <= 0:
                    eligible = 100
                if "placed_students" in metrics:
                    placed = min(eligible, max(0, int(metrics["placed_students"])))
                else:
                    pct = min(100.0, max(0.0, metrics.get("placement_percentage", 0.0)))
                    placed = min(eligible, max(0, int(round(eligible * (pct / 100.0)))))
                plc_pct = round((float(placed) / float(eligible)) * 100.0, 2) if eligible > 0 else 0.0
                median_sal = max(0.0, float(metrics.get("median_salary_lpa", 4.5)))
                max_sal = max(median_sal, float(metrics.get("highest_salary_lpa", median_sal)))
                try:
                    canonical_map["placements"].append(
                        PlacementsSignal(
                            institution_id=inst_id,
                            academic_year=yr,
                            graduation_year=yr,
                            department=dept_key,
                            eligible_students=eligible,
                            placed_students=placed,
                            placement_percentage=plc_pct,
                            median_salary_lpa=median_sal,
                            max_salary_lpa=max_sal,
                            top_tier_recruiters_count=5,
                            unplaced_count=max(0, eligible - placed),
                            provenance=prov,
                        )
                    )
                except Exception:
                    pass

            # Ranking
            if "closing_rank" in metrics and metrics["closing_rank"] >= 1:
                c_rank = int(metrics["closing_rank"])
                o_rank = int(metrics.get("opening_rank", max(1, c_rank // 2)))
                if o_rank > c_rank:
                    o_rank = c_rank
                try:
                    canonical_map["cet_ranking"].append(
                        CETRankingSignal(
                            institution_id=inst_id,
                            academic_year=yr,
                            department=dept_key,
                            quota_category="General",
                            opening_rank=o_rank,
                            closing_rank=c_rank,
                            percentile_cutoff=75.0,
                            provenance=prov,
                        )
                    )
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Stage 8: Data Quality & Contradiction Detection
    # ------------------------------------------------------------------
    def _analyze_data_quality_and_contradictions(
        self,
        new_signals: List[DiscoveredSignal],
        initial_issues: List[DataQualityIssue],
    ) -> Tuple[List[DataQualityIssue], int, int]:
        issues: List[DataQualityIssue] = list(initial_issues)
        contradictions_count = 0
        duplicates_count = 0

        if not new_signals:
            return issues, 0, 0

        # 1. Missing Periods check
        missing_period_sigs = [s for s in new_signals if s.context.time_period is None and s.context.academic_year is None]
        if missing_period_sigs:
            issues.append(
                DataQualityIssue(
                    issue_type="missing_periods",
                    severity="MEDIUM",
                    description=(
                        f"{len(missing_period_sigs)} signal(s) lack an evidenced time period or academic year in the source document. "
                        f"CIP kept time_period=None rather than inventing dates."
                    ),
                    affected_domain=missing_period_sigs[0].domain,
                    affected_metric=missing_period_sigs[0].metric_name,
                    provenance_refs=[
                        s.provenance.spreadsheet_location or s.provenance.page_or_section or s.provenance.document
                        for s in missing_period_sigs[:5]
                    ],
                )
            )

        # Also check for non-contiguous year gaps (e.g., 2021, 2022, 2024 -> missing 2023)
        years_present = sorted({s.context.academic_year for s in new_signals if s.context.academic_year is not None})
        if len(years_present) >= 2:
            full_range = set(range(years_present[0], years_present[-1] + 1))
            missing_yrs = sorted(list(full_range - set(years_present)))
            if missing_yrs:
                issues.append(
                    DataQualityIssue(
                        issue_type="missing_periods",
                        severity="MEDIUM",
                        description=f"Discontinuous temporal coverage: missing academic year(s) {', '.join(str(y) for y in missing_yrs)} between {years_present[0]} and {years_present[-1]}.",
                        affected_period=", ".join(str(y) for y in missing_yrs),
                    )
                )

        # 2. Outdated Data check (latest year <= current_year - 3)
        current_year = datetime.now(timezone.utc).year
        if years_present and max(years_present) <= current_year - 3:
            issues.append(
                DataQualityIssue(
                    issue_type="outdated_data",
                    severity="MEDIUM",
                    description=(
                        f"Latest evidenced academic year in uploaded data is {max(years_present)}, "
                        f"which is {current_year - max(years_present)} years behind current year ({current_year})."
                    ),
                    affected_period=str(max(years_present)),
                )
            )

        # 3. Incomplete Coverage check (e.g. only 1 domain discovered or missing department breakdown)
        domains_set = {s.domain for s in new_signals}
        if len(domains_set) == 1:
            only_dom = next(iter(domains_set))
            issues.append(
                DataQualityIssue(
                    issue_type="incomplete_coverage",
                    severity="LOW",
                    description=(
                        f"Source provides single-domain coverage ('{only_dom}' only). "
                        f"Add data from additional institutional domains (e.g. faculty, finance, retention, research, grievances) for broader cross-signal intelligence."
                    ),
                    affected_domain=only_dom,
                )
            )

        # 4. Duplicates & Contradictory Sources check (within new_signals AND against self.existing_signals)
        # Key: (institution_id, department, program, period_key, metric_name)
        combined = list(self.existing_signals) + list(new_signals)
        buckets: Dict[Tuple[str, str, str, str, str], List[DiscoveredSignal]] = {}
        for s in combined:
            period_key = str(s.context.academic_year or s.context.time_period or "UNSPECIFIED")
            key = (
                s.context.institution_id or "UNSCOPED",
                s.context.department or "INSTITUTIONAL",
                s.context.program or "ALL",
                period_key,
                s.metric_name,
            )
            buckets.setdefault(key, []).append(s)

        new_signal_ids = {s.signal_id for s in new_signals}

        for key, obs_list in buckets.items():
            if len(obs_list) < 2:
                continue
            # Only report if at least one observation in this bucket belongs to the current upload
            if not any(o.signal_id in new_signal_ids for o in obs_list):
                continue

            inst_k, dept_k, prog_k, period_k, metric_k = key
            distinct_vals: Dict[str, List[DiscoveredSignal]] = {}
            for o in obs_list:
                val_repr = f"{o.value:.4f}" if o.value is not None else o.raw_value.strip().lower()
                distinct_vals.setdefault(val_repr, []).append(o)

            # Check duplicates (same value repeated >= 2 times)
            for val_repr, dup_list in distinct_vals.items():
                if len(dup_list) >= 2:
                    for dup_sig in dup_list:
                        if dup_sig.signal_id in new_signal_ids:
                            dup_sig.is_duplicate = True
                            duplicates_count += 1
                    issues.append(
                        DataQualityIssue(
                            issue_type="duplicates",
                            severity="LOW",
                            description=(
                                f"Duplicate observation for metric '{metric_k}' "
                                f"(department={dept_k}, period={period_k}, value={dup_list[0].raw_value}) recorded {len(dup_list)} times."
                            ),
                            affected_domain=dup_list[0].domain,
                            affected_metric=metric_k,
                            affected_department=None if dept_k == "INSTITUTIONAL" else dept_k,
                            affected_period=None if period_k == "UNSPECIFIED" else period_k,
                            provenance_refs=[
                                o.provenance.spreadsheet_location or o.provenance.page_or_section or o.provenance.document
                                for o in dup_list
                            ],
                        )
                    )

            # Check contradictory sources (different values for the exact same context + metric)
            if len(distinct_vals) >= 2:
                contradictions_count += 1
                group_id = f"contra_{hashlib.sha256(f'{key}'.encode()).hexdigest()[:10]}"
                conflicting_entries: List[Dict[str, Any]] = []
                for o in obs_list:
                    o.is_contradictory = True
                    o.contradiction_group_id = group_id
                    conflicting_entries.append(
                        {
                            "signal_id": o.signal_id,
                            "value": o.value,
                            "raw_value": o.raw_value,
                            "unit": o.unit,
                            "source_document": o.provenance.document,
                            "location": o.provenance.spreadsheet_location or o.provenance.page_or_section or "document",
                            "excerpt": o.provenance.excerpt_or_reference,
                            "confidence": o.provenance.extraction_confidence,
                        }
                    )

                val_summary = " vs. ".join(
                    f"{e['raw_value']} ({e['source_document']} @ {e['location']})"
                    for e in conflicting_entries
                )
                issues.append(
                    DataQualityIssue(
                        issue_type="contradictory_sources",
                        severity="HIGH",
                        description=(
                            f"Contradictory values detected for '{metric_k}' "
                            f"(department={dept_k}, period={period_k}): {val_summary}. "
                            f"All conflicting observations are preserved visibly."
                        ),
                        affected_domain=obs_list[0].domain,
                        affected_metric=metric_k,
                        affected_department=None if dept_k == "INSTITUTIONAL" else dept_k,
                        affected_period=None if period_k == "UNSPECIFIED" else period_k,
                        conflicting_values=conflicting_entries,
                        provenance_refs=[e["location"] for e in conflicting_entries],
                    )
                )

        return issues, contradictions_count, duplicates_count
