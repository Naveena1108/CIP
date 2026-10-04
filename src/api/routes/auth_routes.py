"""
Authentication Route Handlers for AI CRISS / CIP API.
Supports Email/Password Authentication, Google OAuth 2.0 (Authorization Code & GIS Token),
Session Logout, and Post-Authentication Entity Onboarding.
"""

import hashlib
import json
import os
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Literal, Optional, Union
from urllib.parse import urlencode, quote
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_db_session
from src.db.repository import (
    AccessPolicyRepository,
    HierarchyNodeRepository,
    InstitutionRepository,
    OrganizationRepository,
    UserRepository,
    OTPRepository,
    RevokedTokenRepository,
)
from src.runtime_env import is_deployed_environment
from src.services.email_service import send_otp_email
from src.db.models import UserModel
from src.taxonomy import (
    determine_structural_archetype,
    get_applicable_profile_fields,
    get_full_taxonomy_catalog,
    is_other_value,
)
from src.api.auth import (
    GOOGLE_CALLBACK_PATH,
    Token,
    UserResponse,
    create_access_token,
    create_google_oauth_state,
    create_password_reset_token,
    get_allowed_google_origins,
    get_current_user,
    get_google_oauth_config,
    get_password_hash,
    oauth2_scheme,
    revoke_access_token,
    verify_google_oauth_state,
    verify_password,
    verify_password_reset_token,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_TOKENINFO_ENDPOINT = "https://oauth2.googleapis.com/tokeninfo"

EntityClassificationLiteral = Literal[
    "educational_institution",
    "institution",
    "university",
    "educational_group_network",
    "government_public_body",
    "private_organization",
    "nonprofit_trust_society",
    "nonprofit_trust_society_foundation",
    "research_academic_body",
    "other",
]


def generate_secure_otp() -> str:
    """Generate a cryptographically secure 6-digit numeric OTP."""
    return f"{secrets.randbelow(900000) + 100000:06d}"


def hash_otp_code(otp: str) -> str:
    """Compute deterministic SHA-256 hash for secure OTP storage and comparison."""
    return hashlib.sha256(otp.strip().encode("utf-8")).hexdigest()


def verify_otp_hash(otp: str, stored_hash: str) -> bool:
    """Constant-time verification of submitted OTP code against persisted hash."""
    return secrets.compare_digest(hash_otp_code(otp), stored_hash)


class OTPChallengeResponse(BaseModel):
    status: Literal["AWAITING_OTP"] = "AWAITING_OTP"
    message: str = "A single-use 6-digit verification code has been dispatched to your email."
    email: EmailStr
    purpose: Literal["login", "signup", "reset_password"]
    expires_in_seconds: int = 300
    resend_cooldown_seconds: int = 60


class OTPVerifyRequest(BaseModel):
    email: EmailStr
    otp: Optional[str] = None
    otp_code: Optional[str] = None
    purpose: str = "login"

    @model_validator(mode="after")
    def validate_code_and_purpose(self) -> "OTPVerifyRequest":
        code = (self.otp or self.otp_code or "").strip()
        if len(code) != 6 or not code.isdigit():
            raise ValueError("Verification code must be exactly 6 numeric digits.")
        self.otp = code
        self.otp_code = code
        p = (self.purpose or "login").strip().lower()
        if p not in ("login", "signup", "reset_password"):
            p = "login"
        self.purpose = p
        return self


class OTPResendRequest(BaseModel):
    email: EmailStr
    purpose: str = "login"

    @model_validator(mode="after")
    def validate_purpose(self) -> "OTPResendRequest":
        p = (self.purpose or "login").strip().lower()
        if p not in ("login", "signup", "reset_password"):
            p = "login"
        self.purpose = p
        return self


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    otp: Optional[str] = None
    skip_otp: bool = False


class SignupRequest(BaseModel):
    """Normal user signup model — never requires role selection."""
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=256)
    full_name: Optional[str] = Field(default=None, max_length=255)
    job_title: Optional[str] = Field(default=None, max_length=128)
    phone: Optional[str] = Field(default=None, max_length=64)
    department_or_unit: Optional[str] = Field(default=None, max_length=128)
    skip_otp: bool = False


class RegisterRequest(BaseModel):
    user_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=256)
    role: Literal["SuperAdmin", "Auditor", "Analyst", "Viewer"] = "Viewer"
    full_name: Optional[str] = Field(default=None, max_length=255)


class PasswordRecoveryRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirmRequest(BaseModel):
    reset_token: str = Field(..., min_length=10)
    new_password: str = Field(..., min_length=6, max_length=256)


class GoogleConfigResponse(BaseModel):
    configured: bool
    client_id: Optional[str] = None
    detected_origin: str
    callback_path: str
    redirect_uri: str
    authorized_javascript_origins: List[str]
    authorized_redirect_uris: List[str]
    authorization_url: Optional[str] = None


class GoogleTokenExchangeRequest(BaseModel):
    credential: Optional[str] = None
    code: Optional[str] = None
    state: Optional[str] = None
    origin: Optional[str] = None
    redirect_uri: Optional[str] = None


class ChildInstitutionSeed(BaseModel):
    institution_id: str = Field(..., min_length=2, max_length=64)
    name: str = Field(..., min_length=2, max_length=255)
    education_entity_type: str = Field(default="College", max_length=128)
    education_entity_type_other: Optional[str] = Field(default=None, max_length=255)
    university_type: Optional[str] = Field(default=None, max_length=128)
    university_type_other: Optional[str] = Field(default=None, max_length=255)
    ownership_governance: Optional[str] = Field(default=None, max_length=128)
    ownership_governance_other: Optional[str] = Field(default=None, max_length=255)
    academic_domains: Optional[List[str]] = Field(default=None)
    academic_domain_other: Optional[str] = Field(default=None, max_length=255)
    state: str = Field(default="Karnataka", max_length=64)
    city: Optional[str] = Field(default=None, max_length=128)
    accreditation_grade: str = Field(default="A", max_length=16)

    @model_validator(mode="after")
    def validate_child_other_fields(self) -> "ChildInstitutionSeed":
        if is_other_value(self.education_entity_type):
            if not self.education_entity_type_other or not self.education_entity_type_other.strip():
                raise ValueError("Please specify custom education/entity type for child institution when 'Other' is selected.")
            self.education_entity_type_other = self.education_entity_type_other.strip()
        if is_other_value(self.university_type):
            if not self.university_type_other or not self.university_type_other.strip():
                raise ValueError("Please specify custom university type for child institution when 'Other' is selected.")
            self.university_type_other = self.university_type_other.strip()
        if is_other_value(self.ownership_governance):
            if not self.ownership_governance_other or not self.ownership_governance_other.strip():
                raise ValueError("Please specify custom ownership/governance for child institution when 'Other' is selected.")
            self.ownership_governance_other = self.ownership_governance_other.strip()
        if self.academic_domains and any(is_other_value(d) for d in self.academic_domains):
            if not self.academic_domain_other or not self.academic_domain_other.strip():
                raise ValueError("Please specify custom academic domain for child institution when 'Other' is selected.")
            self.academic_domain_other = self.academic_domain_other.strip()
        return self


class EntityOnboardingRequest(BaseModel):
    entity_id: Optional[str] = Field(default=None, min_length=2, max_length=64)
    entity_name: str = Field(..., min_length=2, max_length=255)

    # A. Entity Category (progressive primary classification)
    entity_category: Optional[str] = Field(default=None, max_length=64)
    entity_category_other: Optional[str] = Field(default=None, max_length=255)

    # Backward-compatible / canonical entity_type
    entity_type: Optional[EntityClassificationLiteral] = Field(default=None)
    entity_type_other: Optional[str] = Field(default=None, max_length=255)

    # B. Ownership / Governance
    ownership_governance: str = Field(..., min_length=2, max_length=128)
    ownership_governance_other: Optional[str] = Field(default=None, max_length=255)

    # C. Education / Entity Type & University Type
    education_entity_type: Optional[str] = Field(default=None, max_length=128)
    education_entity_type_other: Optional[str] = Field(default=None, max_length=255)
    education_level: Optional[str] = Field(default=None, max_length=128)
    university_type: Optional[str] = Field(default=None, max_length=128)
    university_type_other: Optional[str] = Field(default=None, max_length=255)

    # D. Academic Domains (multi-select list and/or single string)
    academic_domains: Optional[List[str]] = Field(default=None)
    academic_domain: Optional[str] = Field(default=None, max_length=255)
    academic_domain_other: Optional[str] = Field(default=None, max_length=255)

    # Structural & Profile metadata
    parent_organization_id: Optional[str] = Field(default=None, max_length=64)
    state: str = Field(default="Karnataka", max_length=64)
    city: Optional[str] = Field(default=None, max_length=128)
    accreditation_grade: str = Field(default="A", max_length=16)
    established_year: Optional[int] = Field(default=None)
    website: Optional[str] = Field(default=None, max_length=255)
    contact_email: Optional[str] = Field(default=None, max_length=255)
    regulatory_body: Optional[str] = Field(default=None, max_length=128)
    affiliation_details: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = Field(default=None)
    child_institutions: Optional[List[ChildInstitutionSeed]] = Field(default=None)

    @model_validator(mode="after")
    def validate_progressive_classification(self) -> "EntityOnboardingRequest":
        if not self.entity_id or not self.entity_id.strip():
            import re as _re
            slug = _re.sub(r"[^A-Za-z0-9]+", "_", self.entity_name.strip()).strip("_").upper()[:36]
            self.entity_id = f"INST_{slug}_{secrets.token_hex(2).upper()}"
        # Harmonize entity_category and entity_type
        if not self.entity_category and not self.entity_type:
            raise ValueError("Either entity_category or entity_type must be provided.")
        if not self.entity_category and self.entity_type:
            mapping = {
                "institution": "educational_institution",
                "university": "educational_institution",
                "nonprofit_trust_society": "nonprofit_trust_society_foundation",
            }
            self.entity_category = mapping.get(self.entity_type, self.entity_type)

        if not self.entity_type and self.entity_category:
            rev_mapping = {
                "educational_institution": "institution",
                "nonprofit_trust_society_foundation": "nonprofit_trust_society",
            }
            mapped = rev_mapping.get(self.entity_category, self.entity_category)
            if (self.education_entity_type or "").strip().lower() == "university":
                mapped = "university"
            self.entity_type = mapped  # type: ignore[assignment]

        # Harmonize custom "other" between entity_category_other and entity_type_other
        if is_other_value(self.entity_category) or is_other_value(self.entity_type):
            custom_cat = (self.entity_category_other or self.entity_type_other or "").strip()
            if not custom_cat:
                raise ValueError(
                    "Please specify a custom entity classification (entity_category_other / entity_type_other) when 'Other' is selected."
                )
            self.entity_category_other = custom_cat
            self.entity_type_other = custom_cat

        # Validate Ownership/Governance "Other"
        if is_other_value(self.ownership_governance):
            if not self.ownership_governance_other or not self.ownership_governance_other.strip():
                raise ValueError(
                    "Please specify custom ownership/governance (ownership_governance_other) when 'Other' is selected."
                )
            self.ownership_governance_other = self.ownership_governance_other.strip()

        # Harmonize education_entity_type & education_level
        if not self.education_entity_type and self.education_level:
            self.education_entity_type = self.education_level
        if not self.education_level and self.education_entity_type:
            self.education_level = self.education_entity_type
        if not self.education_entity_type:
            if self.entity_type == "university":
                self.education_entity_type = "University"
            else:
                self.education_entity_type = "College"
            self.education_level = self.education_entity_type

        if is_other_value(self.education_entity_type):
            if not self.education_entity_type_other or not self.education_entity_type_other.strip():
                raise ValueError(
                    "Please specify custom education/entity type (education_entity_type_other) when 'Other' is selected."
                )
            self.education_entity_type_other = self.education_entity_type_other.strip()

        # Validate University Type "Other"
        if is_other_value(self.university_type):
            if not self.university_type_other or not self.university_type_other.strip():
                raise ValueError(
                    "Please specify custom university type (university_type_other) when 'Other' is selected."
                )
            self.university_type_other = self.university_type_other.strip()

        # Harmonize academic_domains list & academic_domain string
        if self.academic_domains and not self.academic_domain:
            self.academic_domain = ", ".join(self.academic_domains)
        elif self.academic_domain and not self.academic_domains:
            self.academic_domains = [d.strip() for d in self.academic_domain.split(",") if d.strip()]
        elif not self.academic_domains and not self.academic_domain:
            self.academic_domains = ["General / School Curriculum"]
            self.academic_domain = "General / School Curriculum"

        has_other_domain = is_other_value(self.academic_domain) or (
            self.academic_domains is not None and any(is_other_value(d) for d in self.academic_domains)
        )
        if has_other_domain:
            if not self.academic_domain_other or not self.academic_domain_other.strip():
                raise ValueError(
                    "Please specify custom academic domain (academic_domain_other) when 'Other' is selected."
                )
            self.academic_domain_other = self.academic_domain_other.strip()

        return self


def _detect_request_origin(request: Request) -> str:
    """Determine exact origin (scheme://host[:port]) from request headers or URL."""
    origin_hdr = request.headers.get("origin")
    if origin_hdr:
        return origin_hdr.strip().rstrip("/")
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme or "http"
    netloc = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
        or "localhost:8000"
    )
    return f"{scheme}://{netloc}".rstrip("/")


def _wants_html_redirect(request: Request, format_param: Optional[str]) -> bool:
    if format_param == "json":
        return False
    if format_param == "redirect":
        return True
    accept = (request.headers.get("accept") or "").lower()
    return "text/html" in accept


def _oauth_error_response(
    request: Request,
    format_param: Optional[str],
    status_code: int,
    error_code: str,
    detail: str,
    extra: Optional[Dict[str, Any]] = None,
):
    payload: Dict[str, Any] = {
        "error": error_code,
        "error_code": error_code,
        "detail": detail,
    }
    if extra:
        payload.update(extra)
    if _wants_html_redirect(request, format_param):
        qs = urlencode({"oauth_error": error_code, "oauth_error_detail": detail})
        return RedirectResponse(url=f"/dashboard/?{qs}", status_code=status.HTTP_302_FOUND)
    return JSONResponse(status_code=status_code, content=payload)


async def _exchange_google_code_for_profile(
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> Dict[str, Any]:
    """Exchange OAuth authorization code with Google and retrieve verified user profile."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            token_resp = await client.post(
                GOOGLE_TOKEN_ENDPOINT,
                data={
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Accept": "application/json"},
            )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"provider_failure: Failed to reach Google OAuth token endpoint ({exc}).",
        )

    if token_resp.status_code >= 500:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="provider_failure: Google OAuth token service returned a 5xx provider error.",
        )

    if token_resp.status_code != 200:
        err_body = {}
        try:
            err_body = token_resp.json()
        except Exception:
            pass
        g_err = str(err_body.get("error") or "")
        g_desc = str(err_body.get("error_description") or "Token exchange failed.")
        if "redirect_uri_mismatch" in g_err or "redirect_uri_mismatch" in g_desc.lower():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"redirect_uri_mismatch: Google rejected redirect_uri '{redirect_uri}'. Ensure '{redirect_uri}' is added under Authorized redirect URIs in Google Cloud Console.",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"invalid_or_expired_credential: Google authorization code is invalid or expired ({g_err}: {g_desc}).",
        )

    token_data = token_resp.json()
    access_token = token_data.get("access_token")
    id_token = token_data.get("id_token")

    if not access_token and not id_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_or_expired_credential: Google token response did not contain a valid credential.",
        )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if access_token:
                userinfo_resp = await client.get(
                    GOOGLE_USERINFO_ENDPOINT,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
            else:
                userinfo_resp = await client.get(
                    GOOGLE_TOKENINFO_ENDPOINT,
                    params={"id_token": id_token},
                )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"provider_failure: Failed to fetch Google user profile ({exc}).",
        )

    if userinfo_resp.status_code >= 500:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="provider_failure: Google userinfo service returned a 5xx error.",
        )
    if userinfo_resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_or_expired_credential: Google access/ID token failed verification.",
        )

    profile = userinfo_resp.json()
    return profile


async def _verify_google_id_token(id_token: str, expected_client_id: str) -> Dict[str, Any]:
    """Verify a Google Identity Services ID token (credential) with Google's tokeninfo endpoint."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(GOOGLE_TOKENINFO_ENDPOINT, params={"id_token": id_token})
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"provider_failure: Unable to contact Google token verification service ({exc}).",
        )

    if resp.status_code >= 500:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="provider_failure: Google tokeninfo endpoint returned a 5xx provider error.",
        )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_or_expired_credential: Google ID token is invalid or expired.",
        )

    info = resp.json()
    aud = info.get("aud")
    if aud != expected_client_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_or_expired_credential: Google ID token audience (client_id) mismatch.",
        )
    return info


def _build_user_response(user: UserModel) -> UserResponse:
    onboarding_done = bool(user.onboarding_completed)
    policy = AccessPolicyRepository.get_policy(user)
    return UserResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        auth_provider=user.auth_provider or "local",
        full_name=user.full_name,
        job_title=user.job_title,
        phone=user.phone,
        department_or_unit=user.department_or_unit,
        organization_id=user.organization_id,
        primary_institution_id=user.primary_institution_id,
        onboarding_completed=onboarding_done,
        onboarding_required=not onboarding_done,
        access_policy=policy.model_dump(),
    )


@router.post("/login", response_model=Token)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_db_session)
):
    # form_data.username is the user's email
    user = await UserRepository.get_by_email(session, email=form_data.username)
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Inactive user")

    token_str = create_access_token(
        user.id,
        user.email,
        user.role,
        primary_institution_id=user.primary_institution_id,
        organization_id=user.organization_id,
        onboarding_completed=bool(user.onboarding_completed),
        full_name=user.full_name,
    )
    onboarding_done = bool(user.onboarding_completed)
    return Token(
        access_token=token_str,
        token_type="bearer",
        role=user.role,
        expires_in_seconds=8 * 3600,
        user_id=user.id,
        email=user.email,
        auth_provider=user.auth_provider or "local",
        onboarding_required=not onboarding_done,
        primary_institution_id=user.primary_institution_id,
        organization_id=user.organization_id,
    )


@router.post("/login/json", response_model=Union[OTPChallengeResponse, Token])
async def login_json(
    req: LoginRequest,
    session: AsyncSession = Depends(get_db_session)
):
    """
    JSON email/password login endpoint.
    If OTP is provided in request or skip_otp is True, validates credentials and returns authenticated Token.
    Otherwise, issues a cryptographically secure 6-digit OTP, delivers it via email service,
    and returns an OTPChallengeResponse (status: AWAITING_OTP).
    """
    user = await UserRepository.get_by_email(session, email=req.email)
    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Inactive user")

    # If direct verification with OTP submitted in login request
    if req.otp:
        active_otp = await OTPRepository.get_active_otp(session, email=req.email, purpose="login")
        if not active_otp:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No active verification code found for this account. Please request a new code.",
            )
        now = datetime.now(timezone.utc)
        exp = active_otp.expires_at.replace(tzinfo=timezone.utc) if active_otp.expires_at.tzinfo is None else active_otp.expires_at
        if now > exp:
            await OTPRepository.invalidate_otp(session, active_otp)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Verification code has expired. Please request a new code.",
            )
        attempts = await OTPRepository.increment_attempts(session, active_otp)
        if attempts > 5:
            await OTPRepository.invalidate_otp(session, active_otp)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Too many failed verification attempts. This code has been invalidated. Please request a new code.",
            )
        if not verify_otp_hash(req.otp, active_otp.otp_hash):
            remaining = max(0, 5 - attempts)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid verification code. {remaining} attempt(s) remaining.",
            )
        await OTPRepository.mark_used(session, active_otp)
        token_str = create_access_token(
            user.id,
            user.email,
            user.role,
            primary_institution_id=user.primary_institution_id,
            organization_id=user.organization_id,
            onboarding_completed=bool(user.onboarding_completed),
            full_name=user.full_name,
        )
        onboarding_done = bool(user.onboarding_completed)
        return Token(
            access_token=token_str,
            token_type="bearer",
            role=user.role,
            expires_in_seconds=8 * 3600,
            user_id=user.id,
            email=user.email,
            auth_provider=user.auth_provider or "local",
            onboarding_required=not onboarding_done,
            primary_institution_id=user.primary_institution_id,
            organization_id=user.organization_id,
        )

    # If skip_otp is requested (strictly permitted in local development only)
    if req.skip_otp:
        if is_deployed_environment():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OTP verification cannot be bypassed in deployed environments.",
            )
        token_str = create_access_token(
            user.id,
            user.email,
            user.role,
            primary_institution_id=user.primary_institution_id,
            organization_id=user.organization_id,
            onboarding_completed=bool(user.onboarding_completed),
            full_name=user.full_name,
        )
        onboarding_done = bool(user.onboarding_completed)
        return Token(
            access_token=token_str,
            token_type="bearer",
            role=user.role,
            expires_in_seconds=8 * 3600,
            user_id=user.id,
            email=user.email,
            auth_provider=user.auth_provider or "local",
            onboarding_required=not onboarding_done,
            primary_institution_id=user.primary_institution_id,
            organization_id=user.organization_id,
        )

    # Standard Phase 3 flow: Generate 6-digit OTP, hash, persist, and dispatch
    otp_code = generate_secure_otp()
    otp_hash = hash_otp_code(otp_code)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    await OTPRepository.create_otp(
        session=session,
        email=req.email,
        purpose="login",
        otp_hash=otp_hash,
        expires_at=expires_at,
        user_id=user.id,
    )
    sent = send_otp_email(recipient_email=req.email, otp_code=otp_code, purpose="login")
    if not sent and (is_deployed_environment() or os.environ.get("RESEND_API_KEY") or os.environ.get("SMTP_HOST")):
        err_msg = getattr(sent, "error", None) or "Email delivery failed"
        logger.error(
            f"[AUTH_LOGIN_OTP_EMAIL_FAILED] endpoint=/api/v1/auth/login/json user={req.email} "
            f"operation=OTP_EMAIL_DELIVERY purpose=login provider={getattr(sent, 'provider', 'UNKNOWN')} "
            f"error={err_msg}"
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="We couldn't send the verification code to your email. Please verify your email configuration or contact your administrator."
        )
    return OTPChallengeResponse(
        status="AWAITING_OTP",
        message="A single-use 6-digit verification code has been dispatched to your email.",
        email=req.email,
        purpose="login",
        expires_in_seconds=300,
        resend_cooldown_seconds=60,
        onboarding_required=not bool(user.onboarding_completed),
    )


@router.post("/signup", response_model=Union[OTPChallengeResponse, Token])
async def signup(
    req: SignupRequest,
    session: AsyncSession = Depends(get_db_session)
):
    """
    Normal user signup endpoint.
    Provisions a pending CIP user (is_verified=False), generates a secure 6-digit OTP,
    dispatches email notification, and returns an OTPChallengeResponse (status: AWAITING_OTP).
    When skip_otp=True (testing), verifies immediately and returns an active session token.
    """
    existing = await UserRepository.get_by_email(session, email=req.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User with this email already exists"
        )
    generated_id = f"usr_{uuid.uuid4().hex[:16]}"
    hashed = get_password_hash(req.password)
    user = await UserRepository.create_user(
        session=session,
        user_id=generated_id,
        email=req.email,
        hashed_password=hashed,
        role="Auditor",
        auth_provider="local",
        full_name=req.full_name,
        job_title=req.job_title,
        phone=req.phone,
        department_or_unit=req.department_or_unit,
        onboarding_completed=False,
    )
    if req.skip_otp:
        if is_deployed_environment():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OTP verification cannot be bypassed in deployed environments.",
            )
        user.is_verified = True
        await session.flush()
        token_str = create_access_token(
            user.id,
            user.email,
            user.role,
            primary_institution_id=user.primary_institution_id,
            organization_id=user.organization_id,
            onboarding_completed=False,
            full_name=user.full_name,
        )
        return Token(
            access_token=token_str,
            token_type="bearer",
            role=user.role,
            expires_in_seconds=8 * 3600,
            user_id=user.id,
            email=user.email,
            auth_provider="local",
            onboarding_required=True,
            primary_institution_id=user.primary_institution_id,
            organization_id=user.organization_id,
        )

    # Unverified state until OTP verification
    user.is_verified = False
    await session.flush()

    otp_code = generate_secure_otp()
    otp_hash = hash_otp_code(otp_code)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    await OTPRepository.create_otp(
        session=session,
        email=req.email,
        purpose="signup",
        otp_hash=otp_hash,
        expires_at=expires_at,
        user_id=user.id,
    )
    sent = send_otp_email(recipient_email=req.email, otp_code=otp_code, purpose="signup")
    if not sent and (is_deployed_environment() or os.environ.get("RESEND_API_KEY") or os.environ.get("SMTP_HOST")):
        err_msg = getattr(sent, "error", None) or "Email delivery failed"
        logger.error(
            f"[AUTH_SIGNUP_OTP_EMAIL_FAILED] endpoint=/api/v1/auth/signup user={req.email} "
            f"operation=OTP_EMAIL_DELIVERY purpose=signup provider={getattr(sent, 'provider', 'UNKNOWN')} "
            f"error={err_msg}"
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="We couldn't send the verification code to your email. Please verify your email configuration or contact your administrator."
        )
    return OTPChallengeResponse(
        status="AWAITING_OTP",
        message="A single-use 6-digit verification code has been dispatched to your email.",
        email=req.email,
        purpose="signup",
        expires_in_seconds=300,
        resend_cooldown_seconds=60,
        onboarding_required=True,
    )


@router.post("/otp/verify", response_model=Token)
async def verify_otp(
    req: OTPVerifyRequest,
    session: AsyncSession = Depends(get_db_session)
):
    """
    Verify submitted 6-digit OTP code against the backend database.
    - Validates presence of active OTP for exact email + purpose.
    - Rejects expired OTPs (> 5 minutes).
    - Enforces attempt limits (max 5 attempts, invalidates upon exceeding).
    - Invalidates OTP upon successful verification (single-use).
    - If purpose is 'signup', marks user as is_verified=True.
    - Issues authenticated JWT session token.
    """
    user = await UserRepository.get_by_email(session, email=req.email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found for this email address.",
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Inactive user account.")

    active_otp = await OTPRepository.get_active_otp(session, email=req.email, purpose=req.purpose)
    if not active_otp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active verification code found for this account. Please request a new code.",
        )

    now = datetime.now(timezone.utc)
    exp = active_otp.expires_at.replace(tzinfo=timezone.utc) if active_otp.expires_at.tzinfo is None else active_otp.expires_at
    if now > exp:
        await OTPRepository.invalidate_otp(session, active_otp)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification code has expired. Please request a new code.",
        )

    attempts = await OTPRepository.increment_attempts(session, active_otp)
    if attempts > 5:
        await OTPRepository.invalidate_otp(session, active_otp)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Too many failed verification attempts. This code has been invalidated. Please request a new code.",
        )

    if not verify_otp_hash(req.otp, active_otp.otp_hash):
        remaining = max(0, 5 - attempts)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid verification code. {remaining} attempt(s) remaining.",
        )

    # Invalidate / mark used immediately
    await OTPRepository.mark_used(session, active_otp)

    # Mark user verified if signup
    if req.purpose == "signup":
        user.is_verified = True
        await session.flush()

    token_str = create_access_token(
        user.id,
        user.email,
        user.role,
        primary_institution_id=user.primary_institution_id,
        organization_id=user.organization_id,
        onboarding_completed=bool(user.onboarding_completed),
        full_name=user.full_name,
    )
    onboarding_done = bool(user.onboarding_completed)
    return Token(
        access_token=token_str,
        token_type="bearer",
        role=user.role,
        expires_in_seconds=8 * 3600,
        user_id=user.id,
        email=user.email,
        auth_provider=user.auth_provider or "local",
        onboarding_required=not onboarding_done,
        primary_institution_id=user.primary_institution_id,
        organization_id=user.organization_id,
    )


@router.post("/otp/resend")
async def resend_otp(
    req: OTPResendRequest,
    session: AsyncSession = Depends(get_db_session)
):
    """
    Resend verification code with a 60-second cooldown enforcement.
    Invalidates any previous active OTP, generates a fresh 6-digit code,
    dispatches email, and returns safe metadata.
    """
    user = await UserRepository.get_by_email(session, email=req.email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found for this email address.",
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Inactive user account.")

    # Check 60-second cooldown from latest OTP creation
    latest_otp = await OTPRepository.get_latest_otp(session, email=req.email, purpose=req.purpose)
    now = datetime.now(timezone.utc)
    if latest_otp and latest_otp.created_at:
        created_at = latest_otp.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        elapsed = (now - created_at).total_seconds()
        if elapsed < 60:
            remaining = int(60 - elapsed)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Please wait {remaining} seconds before requesting a new verification code.",
            )

    # Invalidate existing active OTPs
    await OTPRepository.invalidate_all_for_email(session, email=req.email, purpose=req.purpose)

    # Generate new code
    otp_code = generate_secure_otp()
    otp_hash = hash_otp_code(otp_code)
    expires_at = now + timedelta(minutes=5)
    await OTPRepository.create_otp(
        session=session,
        email=req.email,
        purpose=req.purpose,
        otp_hash=otp_hash,
        expires_at=expires_at,
        user_id=user.id,
    )
    sent = send_otp_email(recipient_email=req.email, otp_code=otp_code, purpose=req.purpose)
    if not sent and (is_deployed_environment() or os.environ.get("RESEND_API_KEY") or os.environ.get("SMTP_HOST")):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="We couldn't send the verification code to your email. Please verify your email configuration or contact your administrator."
        )
    return {
        "status": "SENT",
        "message": "A new verification code has been dispatched to your email.",
        "email": req.email,
        "purpose": req.purpose,
        "expires_in_seconds": 300,
        "resend_cooldown_seconds": 60,
    }


@router.get("/session", response_model=UserResponse)
async def get_current_session(
    current_user: UserModel = Depends(get_current_user),
):
    """
    Validate and restore an active authenticated session.
    Alias to /auth/me for frontend session restoration.
    """
    return _build_user_response(current_user)


@router.post("/register", response_model=UserResponse)
async def register(
    req: RegisterRequest,
    session: AsyncSession = Depends(get_db_session)
):
    if is_deployed_environment():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Direct user registration is disabled in deployed environments. Use /api/v1/auth/signup.",
        )
    existing = await UserRepository.get_by_email(session, email=req.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User with this email already exists"
        )
    hashed = get_password_hash(req.password)
    effective_id = req.user_id or f"usr_{uuid.uuid4().hex[:16]}"
    user = await UserRepository.create_user(
        session,
        user_id=effective_id,
        email=req.email,
        hashed_password=hashed,
        role=req.role,
        full_name=req.full_name,
    )
    return _build_user_response(user)


@router.post("/password-recovery/request")
async def request_password_recovery(
    req: PasswordRecoveryRequest,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Issue a signed, time-limited password recovery token for an existing account.
    """
    user = await UserRepository.get_by_email(session, email=req.email)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active CIP account found for this email address.",
        )
    reset_token = create_password_reset_token(user.id, user.email)
    send_otp_email(recipient_email=user.email, otp_code=reset_token[:6], purpose="reset_password")
    is_deployed = is_deployed_environment()
    return {
        "status": "RECOVERY_TOKEN_ISSUED",
        "email": user.email,
        "reset_token": reset_token if not is_deployed else None,
        "message": "Password recovery instructions have been dispatched to your email." if is_deployed else "Recovery token issued.",
        "expires_in_seconds": 900,
    }


@router.post("/password-recovery/reset")
async def confirm_password_reset(
    req: PasswordResetConfirmRequest,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Reset a user's password using a verified recovery token and revoke the token upon use.
    """
    try:
        token_info = verify_password_reset_token(req.reset_token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    user = await UserRepository.get_by_email(session, email=token_info["email"])
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User account not found or inactive.",
        )
    new_hash = get_password_hash(req.new_password)
    await UserRepository.update_password(session, user, new_hash)
    revoke_access_token(req.reset_token)
    return {
        "status": "PASSWORD_RESET_COMPLETED",
        "user_id": user.id,
        "email": user.email,
    }


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: UserModel = Depends(get_current_user)):
    return _build_user_response(current_user)


@router.post("/logout")
async def logout(
    token: str = Depends(oauth2_scheme),
    current_user: UserModel = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """Terminate the active CIP session and revoke the current Bearer token."""
    revoke_access_token(token)
    await RevokedTokenRepository.revoke_token(session, token)
    return {
        "status": "LOGGED_OUT",
        "user_id": current_user.id,
        "email": current_user.email,
    }


@router.get("/google/config", response_model=GoogleConfigResponse)
async def google_oauth_config(request: Request):
    """
    Inspect active Google OAuth configuration, detected frontend origin,
    and exact callback/redirect URIs (never exposes GOOGLE_CLIENT_SECRET).
    """
    detected_origin = _detect_request_origin(request)
    allowed_origins = get_allowed_google_origins()
    effective_origin = detected_origin if detected_origin in allowed_origins else allowed_origins[0]
    cfg = get_google_oauth_config(effective_origin)

    redirect_uris = [f"{o}{GOOGLE_CALLBACK_PATH}" for o in allowed_origins]
    if cfg["redirect_uri"] and cfg["redirect_uri"] not in redirect_uris:
        redirect_uris.append(cfg["redirect_uri"])

    configured = bool(cfg["client_id"] and cfg["client_secret"])
    auth_url: Optional[str] = None
    if cfg["client_id"]:
        state = create_google_oauth_state(effective_origin, cfg["redirect_uri"] or "")
        params = {
            "client_id": cfg["client_id"],
            "redirect_uri": cfg["redirect_uri"],
            "response_type": "code",
            "scope": "openid email profile",
            "access_type": "online",
            "prompt": "select_account",
            "state": state,
        }
        auth_url = f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}"

    return GoogleConfigResponse(
        configured=configured,
        client_id=cfg["client_id"],
        detected_origin=detected_origin,
        callback_path=GOOGLE_CALLBACK_PATH,
        redirect_uri=cfg["redirect_uri"] or f"{effective_origin}{GOOGLE_CALLBACK_PATH}",
        authorized_javascript_origins=allowed_origins,
        authorized_redirect_uris=redirect_uris,
        authorization_url=auth_url,
    )


@router.get("/google/login")
async def google_oauth_login(
    request: Request,
    redirect_uri: Optional[str] = Query(default=None),
    origin: Optional[str] = Query(default=None),
    format: Optional[str] = Query(default=None),
):
    """
    Initiate Google OAuth 2.0 Authorization Code sign-in.
    Validates origin, redirect_uri, and Google Cloud credentials before redirecting.
    Never falls back to a fake user.
    """
    detected_origin = (origin or _detect_request_origin(request)).strip().rstrip("/")
    allowed_origins = get_allowed_google_origins()

    if detected_origin not in allowed_origins:
        return _oauth_error_response(
            request,
            format,
            status.HTTP_400_BAD_REQUEST,
            "origin_mismatch",
            f"Origin '{detected_origin}' is not authorized. Allowed JavaScript origins: {allowed_origins}",
            {"authorized_javascript_origins": allowed_origins},
        )

    cfg = get_google_oauth_config(detected_origin)
    expected_redirect = cfg["redirect_uri"] or f"{detected_origin}{GOOGLE_CALLBACK_PATH}"

    if redirect_uri and redirect_uri.strip() != expected_redirect:
        return _oauth_error_response(
            request,
            format,
            status.HTTP_400_BAD_REQUEST,
            "redirect_uri_mismatch",
            f"Provided redirect_uri '{redirect_uri}' does not match application redirect_uri '{expected_redirect}'.",
            {"expected_redirect_uri": expected_redirect},
        )

    if not cfg["client_id"] or not cfg["client_secret"]:
        return _oauth_error_response(
            request,
            format,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "missing_configuration",
            "Google OAuth credentials (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET) are not configured in the application environment.",
            {
                "authorized_javascript_origins": allowed_origins,
                "authorized_redirect_uris": [f"{o}{GOOGLE_CALLBACK_PATH}" for o in allowed_origins],
                "expected_redirect_uri": expected_redirect,
            },
        )

    state = create_google_oauth_state(detected_origin, expected_redirect)
    params = {
        "client_id": cfg["client_id"],
        "redirect_uri": expected_redirect,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "online",
        "prompt": "select_account",
        "state": state,
    }
    authorization_url = f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}"

    if format == "json":
        return {
            "authorization_url": authorization_url,
            "origin": detected_origin,
            "redirect_uri": expected_redirect,
            "state": state,
        }

    return RedirectResponse(url=authorization_url, status_code=status.HTTP_302_FOUND)


@router.get("/google/callback")
async def google_oauth_callback(
    request: Request,
    code: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
    error_description: Optional[str] = Query(default=None),
    redirect_uri: Optional[str] = Query(default=None),
    format: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Google OAuth 2.0 Authorization Code callback endpoint.
    Handles user cancellation, origin/redirect mismatches, expired/invalid tokens,
    duplicate account checks, and provider failures explicitly.
    """
    if error:
        err_lower = error.lower()
        if err_lower == "access_denied":
            return _oauth_error_response(
                request,
                format,
                status.HTTP_400_BAD_REQUEST,
                "cancelled_login",
                "Google Sign-In was cancelled by the user.",
            )
        if "redirect_uri_mismatch" in err_lower:
            return _oauth_error_response(
                request,
                format,
                status.HTTP_400_BAD_REQUEST,
                "redirect_uri_mismatch",
                error_description or "Google reported a redirect_uri_mismatch.",
            )
        if "origin_mismatch" in err_lower:
            return _oauth_error_response(
                request,
                format,
                status.HTTP_400_BAD_REQUEST,
                "origin_mismatch",
                error_description or "Google reported an origin_mismatch.",
            )
        return _oauth_error_response(
            request,
            format,
            status.HTTP_502_BAD_GATEWAY,
            "provider_failure",
            f"Google OAuth error '{error}': {error_description or 'Authentication failed at provider.'}",
        )

    if not code or not state:
        return _oauth_error_response(
            request,
            format,
            status.HTTP_400_BAD_REQUEST,
            "invalid_or_expired_credential",
            "Missing OAuth authorization code or state parameter in callback.",
        )

    detected_origin = _detect_request_origin(request)
    cfg = get_google_oauth_config(detected_origin)
    expected_redirect = redirect_uri or cfg["redirect_uri"] or f"{detected_origin}{GOOGLE_CALLBACK_PATH}"

    try:
        state_data = verify_google_oauth_state(
            state_token=state,
            expected_origin=detected_origin if detected_origin in get_allowed_google_origins() else None,
            expected_redirect_uri=expected_redirect,
        )
    except ValueError as exc:
        msg = str(exc)
        if msg.startswith("origin_mismatch:"):
            return _oauth_error_response(request, format, status.HTTP_400_BAD_REQUEST, "origin_mismatch", msg)
        if msg.startswith("redirect_uri_mismatch:"):
            return _oauth_error_response(request, format, status.HTTP_400_BAD_REQUEST, "redirect_uri_mismatch", msg)
        return _oauth_error_response(
            request, format, status.HTTP_401_UNAUTHORIZED, "invalid_or_expired_credential", msg
        )

    if detected_origin not in get_allowed_google_origins():
        return _oauth_error_response(
            request,
            format,
            status.HTTP_400_BAD_REQUEST,
            "origin_mismatch",
            f"Callback request origin '{detected_origin}' is not in Authorized JavaScript origins.",
        )

    if not cfg["client_id"] or not cfg["client_secret"]:
        return _oauth_error_response(
            request,
            format,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "missing_configuration",
            "Google OAuth credentials (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET) are missing.",
        )

    try:
        profile = await _exchange_google_code_for_profile(
            code=code,
            client_id=cfg["client_id"],
            client_secret=cfg["client_secret"],
            redirect_uri=state_data["redirect_uri"],
        )
    except HTTPException as exc:
        detail_str = str(exc.detail)
        code_key = "provider_failure"
        for k in ("redirect_uri_mismatch", "origin_mismatch", "invalid_or_expired_credential", "provider_failure"):
            if detail_str.startswith(k):
                code_key = k
                break
        return _oauth_error_response(request, format, exc.status_code, code_key, detail_str)

    google_sub = str(profile.get("sub") or "").strip()
    email = str(profile.get("email") or "").strip().lower()
    email_verified = profile.get("email_verified", True)
    full_name = profile.get("name")

    if not google_sub or not email or str(email_verified).lower() == "false":
        return _oauth_error_response(
            request,
            format,
            status.HTTP_401_UNAUTHORIZED,
            "invalid_or_expired_credential",
            "Google profile did not return a verified email address and subject identifier.",
        )

    try:
        user, _ = await UserRepository.authenticate_or_create_google_user(
            session=session,
            google_sub=google_sub,
            email=email,
            full_name=full_name,
        )
    except ValueError as exc:
        return _oauth_error_response(
            request,
            format,
            status.HTTP_409_CONFLICT,
            "duplicate_account_conflict",
            str(exc),
        )

    if not user.is_active:
        return _oauth_error_response(
            request,
            format,
            status.HTTP_403_FORBIDDEN,
            "invalid_or_expired_credential",
            "User account is inactive.",
        )

    token_str = create_access_token(
        user.id,
        user.email,
        user.role,
        primary_institution_id=user.primary_institution_id,
        organization_id=user.organization_id,
        onboarding_completed=bool(user.onboarding_completed),
        full_name=user.full_name,
    )
    onboarding_required = not bool(user.onboarding_completed)

    if _wants_html_redirect(request, format):
        fragment = urlencode(
            {
                "oauth_token": token_str,
                "role": user.role,
                "email": user.email,
                "user_id": user.id,
                "onboarding_required": "true" if onboarding_required else "false",
                "onboarding_completed": "true" if user.onboarding_completed else "false",
                "primary_institution_id": user.primary_institution_id or "",
            }
        )
        return RedirectResponse(url=f"/dashboard/?{fragment}#{fragment}", status_code=status.HTTP_302_FOUND)

    return Token(
        access_token=token_str,
        token_type="bearer",
        role=user.role,
        expires_in_seconds=8 * 3600,
        user_id=user.id,
        email=user.email,
        auth_provider=user.auth_provider or "google",
        onboarding_required=onboarding_required,
        primary_institution_id=user.primary_institution_id,
        organization_id=user.organization_id,
    )


@router.post("/google/token", response_model=Token)
async def google_oauth_token_exchange(
    req: GoogleTokenExchangeRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Authenticate with a Google Identity Services credential (ID token) or OAuth code via JSON POST.
    """
    detected_origin = (req.origin or _detect_request_origin(request)).strip().rstrip("/")
    allowed_origins = get_allowed_google_origins()
    if detected_origin not in allowed_origins:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"origin_mismatch: Origin '{detected_origin}' is not in Authorized JavaScript origins {allowed_origins}.",
        )

    cfg = get_google_oauth_config(detected_origin)
    expected_redirect = cfg["redirect_uri"] or f"{detected_origin}{GOOGLE_CALLBACK_PATH}"
    if req.redirect_uri and req.redirect_uri != expected_redirect:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"redirect_uri_mismatch: Redirect URI '{req.redirect_uri}' does not match '{expected_redirect}'.",
        )

    if not cfg["client_id"]:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="missing_configuration: GOOGLE_CLIENT_ID is not configured.",
        )

    if req.credential:
        profile = await _verify_google_id_token(req.credential, cfg["client_id"])
    elif req.code:
        if not cfg["client_secret"]:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="missing_configuration: GOOGLE_CLIENT_SECRET is required for code exchange.",
            )
        profile = await _exchange_google_code_for_profile(
            code=req.code,
            client_id=cfg["client_id"],
            client_secret=cfg["client_secret"],
            redirect_uri=expected_redirect,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_or_expired_credential: Either 'credential' or 'code' must be provided.",
        )

    google_sub = str(profile.get("sub") or "").strip()
    email = str(profile.get("email") or "").strip().lower()
    full_name = profile.get("name")

    if not google_sub or not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_or_expired_credential: Invalid Google identity payload.",
        )

    try:
        user, _ = await UserRepository.authenticate_or_create_google_user(
            session=session,
            google_sub=google_sub,
            email=email,
            full_name=full_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user")

    token_str = create_access_token(
        user.id,
        user.email,
        user.role,
        primary_institution_id=user.primary_institution_id,
        organization_id=user.organization_id,
        onboarding_completed=bool(user.onboarding_completed),
        full_name=user.full_name,
    )
    onboarding_required = not bool(user.onboarding_completed)

    return Token(
        access_token=token_str,
        token_type="bearer",
        role=user.role,
        expires_in_seconds=8 * 3600,
        user_id=user.id,
        email=user.email,
        auth_provider=user.auth_provider or "google",
        onboarding_required=onboarding_required,
        primary_institution_id=user.primary_institution_id,
        organization_id=user.organization_id,
    )


@router.get("/onboarding/taxonomy")
async def get_onboarding_taxonomy():
    """Return the complete progressive entity classification catalog for CIP Phase 1."""
    return get_full_taxonomy_catalog()


@router.post("/onboarding")
async def complete_entity_onboarding(
    req: EntityOnboardingRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: UserModel = Depends(get_current_user),
):
    """
    Complete CIP entity onboarding for a newly authenticated user (email/password or Google).
    Supports progressive classification across:
    - Entity Category (+ Other)
    - Ownership / Governance (+ Other)
    - Education / Entity Type (+ Other) & University Type (+ Other)
    - Academic Domains (multi-select + Other)
    - Optional initial Child Institutions for Organization/Group entities
    Enforces strict user and organization isolation.
    """
    existing_inst = await InstitutionRepository.get_by_id(session, req.entity_id)
    if existing_inst and not InstitutionRepository.user_can_access(existing_inst, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: entity ID is already registered to another organization or user.",
        )

    existing_org = await OrganizationRepository.get_by_id(session, req.entity_id)
    if existing_org and not OrganizationRepository.user_can_access(existing_org, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: organization ID is already registered to another user.",
        )

    org_id = req.parent_organization_id or current_user.organization_id or req.entity_id

    if req.parent_organization_id:
        parent_org = await OrganizationRepository.get_by_id(session, req.parent_organization_id)
        if parent_org and not OrganizationRepository.user_can_access(parent_org, current_user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: parent organization belongs to another user or organization.",
            )
        if not parent_org:
            await OrganizationRepository.upsert(
                session=session,
                org_id=req.parent_organization_id,
                name=req.parent_organization_id,
                entity_category="educational_group",
                ownership_governance=req.ownership_governance,
                ownership_governance_other=req.ownership_governance_other,
                education_entity_type="Group / Trust / Society",
                academic_domains=req.academic_domains,
                academic_domain_other=req.academic_domain_other,
                state=req.state,
                city=req.city,
                website=req.website,
                owner_user_id=current_user.id,
            )

    # Upsert the top-level Organization record so Organization Profile is always available
    if not req.parent_organization_id:
        await OrganizationRepository.upsert(
            session=session,
            org_id=org_id,
            name=req.entity_name,
            entity_category=req.entity_category or "educational_institution",
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

    inst = await InstitutionRepository.upsert(
        session=session,
        institution_id=req.entity_id,
        name=req.entity_name,
        state=req.state,
        city=req.city,
        grade=req.accreditation_grade,
        entity_category=req.entity_category,
        entity_category_other=req.entity_category_other,
        entity_type=req.entity_type,
        entity_type_other=req.entity_type_other,
        ownership_governance=req.ownership_governance,
        ownership_governance_other=req.ownership_governance_other,
        education_level=req.education_level,
        education_entity_type=req.education_entity_type,
        education_entity_type_other=req.education_entity_type_other,
        university_type=req.university_type,
        university_type_other=req.university_type_other,
        academic_domain=req.academic_domain,
        academic_domains=req.academic_domains,
        academic_domain_other=req.academic_domain_other,
        parent_organization_id=req.parent_organization_id,
        organization_id=org_id,
        owner_user_id=current_user.id,
        established_year=req.established_year,
        website=req.website,
        contact_email=req.contact_email,
        regulatory_body=req.regulatory_body,
        affiliation_details=req.affiliation_details,
    )

    created_children = []
    if req.child_institutions:
        for child in req.child_institutions:
            existing_child = await InstitutionRepository.get_by_id(session, child.institution_id)
            if existing_child and not InstitutionRepository.user_can_access(existing_child, current_user):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Access denied: child institution ID '{child.institution_id}' is already registered to another user.",
                )
            child_etype = "university" if child.education_entity_type.lower() == "university" else "institution"
            c_inst = await InstitutionRepository.upsert(
                session=session,
                institution_id=child.institution_id,
                name=child.name,
                state=child.state,
                city=child.city,
                grade=child.accreditation_grade,
                entity_category="educational_institution",
                entity_type=child_etype,
                entity_type_other=child.education_entity_type_other,
                ownership_governance=child.ownership_governance or req.ownership_governance,
                ownership_governance_other=child.ownership_governance_other or req.ownership_governance_other,
                education_level=child.education_entity_type,
                education_entity_type=child.education_entity_type,
                education_entity_type_other=child.education_entity_type_other,
                university_type=child.university_type,
                university_type_other=child.university_type_other,
                academic_domains=child.academic_domains or req.academic_domains,
                academic_domain_other=child.academic_domain_other,
                parent_organization_id=req.entity_id,
                organization_id=org_id,
                owner_user_id=current_user.id,
            )
            created_children.append(
                {
                    "id": c_inst.id,
                    "name": c_inst.name,
                    "education_entity_type": c_inst.education_entity_type,
                    "education_entity_type_other": c_inst.education_entity_type_other,
                    "parent_organization_id": c_inst.parent_organization_id,
                    "organization_id": c_inst.organization_id,
                }
            )

    primary_inst_id = created_children[0]["id"] if created_children else inst.id
    updated_user = await UserRepository.complete_onboarding(
        session=session,
        user=current_user,
        institution_id=primary_inst_id,
        organization_id=org_id,
    )

    applicable = get_applicable_profile_fields(
        inst.entity_category, inst.entity_type, inst.education_entity_type
    )
    domains_list = json.loads(inst.academic_domains_json) if inst.academic_domains_json else (
        [d.strip() for d in (inst.academic_domain or "").split(",") if d.strip()]
    )

    refreshed_token = create_access_token(
        user_id=updated_user.id,
        email=updated_user.email,
        role=updated_user.role,
        primary_institution_id=primary_inst_id,
        organization_id=org_id,
        onboarding_completed=True,
        full_name=updated_user.full_name,
    )

    return {
        "status": "ONBOARDING_COMPLETED",
        "access_token": refreshed_token,
        "primary_institution_id": primary_inst_id,
        "organization_id": org_id,
        "user": _build_user_response(updated_user),
        "entity": {
            "id": inst.id,
            "name": inst.name,
            "state": inst.state,
            "city": inst.city,
            "accreditation_grade": inst.accreditation_grade,
            "entity_category": inst.entity_category,
            "entity_category_other": inst.entity_category_other,
            "entity_type": inst.entity_type,
            "entity_type_other": inst.entity_type_other,
            "ownership_governance": inst.ownership_governance,
            "ownership_governance_other": inst.ownership_governance_other,
            "education_level": inst.education_level,
            "education_entity_type": inst.education_entity_type,
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
        },
        "child_institutions": created_children,
    }

