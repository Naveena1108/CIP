"""
Authentication and Role-Based Access Control (RBAC) Module for AI CRISS.
Complies with DDR Module 13 specifications.
Uses direct bcrypt for password hashing (Python 3.13 compatible, avoids passlib wrap bug)
and python-jose for JWT tokens.
"""

import os
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set
import bcrypt
from pydantic import BaseModel, EmailStr
from jose import JWTError, jwt
import secrets
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from src.runtime_env import is_deployed_environment
from src.db.session import get_db_session
from src.db.repository import (
    UserRepository,
    InstitutionRepository,
    OrganizationRepository,
    RevokedTokenRepository,
)
from src.db.models import UserModel


def load_env_files() -> None:
    """Load environment variables from project .env and ~/.env without overwriting explicit env vars."""
    project_root = Path(__file__).resolve().parents[2]
    candidates = [project_root / ".env", Path.home() / ".env"]
    for env_path in candidates:
        if env_path.is_file():
            try:
                for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key and (key not in os.environ or not os.environ[key]):
                        os.environ[key] = val
            except Exception:
                pass


load_env_files()

_insecure_default_key = "aicriss-insecure-test-secret-key-change-in-prod-1234567890"
_env_secret = (os.getenv("JWT_SECRET_KEY") or "").strip()

if is_deployed_environment():
    if not _env_secret or _env_secret == _insecure_default_key:
        raise RuntimeError(
            "CRITICAL SECURITY CONFIGURATION ERROR: A secure, non-default JWT_SECRET_KEY "
            "environment variable must be configured in deployed environments."
        )
    SECRET_KEY = _env_secret
else:
    SECRET_KEY = _env_secret or _insecure_default_key

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 8

DEFAULT_ALLOWED_GOOGLE_ORIGINS: List[str] = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
GOOGLE_CALLBACK_PATH = "/api/v1/auth/google/callback"

# In-memory revoked token registry for immediate server-side logout enforcement
REVOKED_TOKENS: Set[str] = set()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    expires_in_seconds: int
    user_id: Optional[str] = None
    email: Optional[str] = None
    auth_provider: str = "local"
    onboarding_required: bool = False
    primary_institution_id: Optional[str] = None
    organization_id: Optional[str] = None


class TokenPayload(BaseModel):
    sub: str
    email: str
    role: str
    exp: int


class UserResponse(BaseModel):
    id: str
    email: str
    role: str
    is_active: bool
    auth_provider: str = "local"
    full_name: Optional[str] = None
    job_title: Optional[str] = None
    phone: Optional[str] = None
    department_or_unit: Optional[str] = None
    organization_id: Optional[str] = None
    primary_institution_id: Optional[str] = None
    onboarding_completed: bool = False
    onboarding_required: bool = False
    access_policy: Optional[Dict[str, object]] = None


def get_allowed_google_origins() -> List[str]:
    extra = os.getenv("GOOGLE_ALLOWED_ORIGINS", "")
    origins = list(DEFAULT_ALLOWED_GOOGLE_ORIGINS)
    for item in extra.split(","):
        cleaned = item.strip().rstrip("/")
        if cleaned and cleaned not in origins:
            origins.append(cleaned)
    vercel_url = os.getenv("VERCEL_URL", "").strip()
    if vercel_url:
        v_origin = f"https://{vercel_url}".rstrip("/")
        if v_origin not in origins:
            origins.append(v_origin)
    return origins


def get_google_oauth_config(detected_origin: str = "http://localhost:8000") -> Dict[str, Optional[str]]:
    clean_origin = detected_origin.strip().rstrip("/")
    client_id = (os.getenv("GOOGLE_CLIENT_ID") or "").strip() or None
    client_secret = (os.getenv("GOOGLE_CLIENT_SECRET") or "").strip() or None
    explicit_redirect = (os.getenv("GOOGLE_REDIRECT_URI") or "").strip() or None
    redirect_uri = explicit_redirect or f"{clean_origin}{GOOGLE_CALLBACK_PATH}"
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "origin": clean_origin,
        "callback_path": GOOGLE_CALLBACK_PATH,
        "redirect_uri": redirect_uri,
    }


def create_google_oauth_state(origin: str, redirect_uri: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    payload = {
        "purpose": "google_oauth_state",
        "origin": origin.rstrip("/"),
        "redirect_uri": redirect_uri,
        "exp": int(expire.timestamp()),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_google_oauth_state(
    state_token: str,
    expected_origin: Optional[str] = None,
    expected_redirect_uri: Optional[str] = None,
) -> Dict[str, str]:
    try:
        payload = jwt.decode(state_token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise ValueError(f"invalid_or_expired_credential: OAuth state token is invalid or expired ({exc}).")

    if payload.get("purpose") != "google_oauth_state":
        raise ValueError("invalid_or_expired_credential: Invalid OAuth state token purpose.")

    state_origin = (payload.get("origin") or "").rstrip("/")
    state_redirect = payload.get("redirect_uri") or ""
    allowed_origins = get_allowed_google_origins()

    if state_origin not in allowed_origins:
        raise ValueError(
            f"origin_mismatch: State origin '{state_origin}' is not in Authorized JavaScript origins {allowed_origins}."
        )
    if expected_origin and expected_origin.rstrip("/") != state_origin:
        raise ValueError(
            f"origin_mismatch: Callback origin '{expected_origin}' does not match initiating origin '{state_origin}'."
        )
    if expected_redirect_uri and expected_redirect_uri != state_redirect:
        raise ValueError(
            f"redirect_uri_mismatch: Callback redirect_uri '{expected_redirect_uri}' does not match '{state_redirect}'."
        )
    return {"origin": state_origin, "redirect_uri": state_redirect}


def create_password_reset_token(user_id: str, email: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    payload = {
        "purpose": "password_reset",
        "sub": user_id,
        "email": email,
        "exp": int(expire.timestamp()),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_password_reset_token(reset_token: str) -> Dict[str, str]:
    if reset_token in REVOKED_TOKENS:
        raise ValueError("Password reset token has already been used or revoked.")
    try:
        payload = jwt.decode(reset_token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise ValueError(f"Invalid or expired password reset token ({exc}).")
    if payload.get("purpose") != "password_reset":
        raise ValueError("Invalid token purpose for password recovery.")
    email = payload.get("email")
    sub = payload.get("sub")
    if not email or not sub:
        raise ValueError("Malformed password reset token.")
    return {"user_id": sub, "email": email}


def revoke_access_token(token: str) -> None:
    REVOKED_TOKENS.add(token)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not hashed_password or hashed_password.startswith("!GOOGLE_OAUTH"):
        return False
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8")
        )
    except Exception:
        return False


def get_password_hash(password: str) -> str:
    # bcrypt max length is 72 bytes
    pwd_bytes = password.encode("utf-8")[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def create_access_token(
    user_id: str,
    email: str,
    role: str,
    expires_delta: Optional[timedelta] = None,
    primary_institution_id: Optional[str] = None,
    organization_id: Optional[str] = None,
    onboarding_completed: Optional[bool] = None,
    full_name: Optional[str] = None,
) -> str:
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS))
    to_encode: Dict[str, object] = {
        "sub": user_id,
        "email": email,
        "role": role,
        "iat": int(datetime.now(timezone.utc).timestamp()),
        "exp": int(expire.timestamp()),
    }
    if primary_institution_id is not None:
        to_encode["primary_institution_id"] = primary_institution_id
    if organization_id is not None:
        to_encode["organization_id"] = organization_id
    if onboarding_completed is not None:
        to_encode["onboarding_completed"] = bool(onboarding_completed)
    if full_name is not None:
        to_encode["full_name"] = full_name
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_db_session)
) -> UserModel:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if token in REVOKED_TOKENS or await RevokedTokenRepository.is_token_revoked(session, token):
        REVOKED_TOKENS.add(token)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has been logged out. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        email: str = payload.get("email")
        if user_id is None or email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = await UserRepository.get_by_email(session, email=email)
    if user is None:
        user = await UserRepository.get_by_id(session, user_id=user_id)
    if user is None:
        # Serverless / multi-worker deployments using per-instance SQLite (/tmp/ai_criss.db)
        # may route a subsequent request with a valid signed JWT to a worker that has not yet
        # materialized this authenticated user row. Re-hydrate from the verified JWT claims.
        user = await UserRepository.create_user(
            session=session,
            user_id=user_id,
            email=email,
            hashed_password="!JWT_SESSION_HYDRATED",
            role=str(payload.get("role") or "Auditor"),
            auth_provider="jwt_session",
            full_name=payload.get("full_name"),
            organization_id=payload.get("organization_id"),
            primary_institution_id=payload.get("primary_institution_id"),
            onboarding_completed=bool(payload.get("onboarding_completed", False)),
        )
    else:
        if payload.get("onboarding_completed") and not user.onboarding_completed:
            user.onboarding_completed = True
        if payload.get("primary_institution_id") and not user.primary_institution_id:
            user.primary_institution_id = str(payload.get("primary_institution_id"))
        if payload.get("organization_id") and not user.organization_id:
            user.organization_id = str(payload.get("organization_id"))
        await session.flush()

    if user.primary_institution_id:
        existing_inst = await InstitutionRepository.get_by_id(session, user.primary_institution_id)
        if existing_inst is None:
            await InstitutionRepository.upsert(
                session=session,
                institution_id=user.primary_institution_id,
                name=user.primary_institution_id,
                owner_user_id=user.id,
                organization_id=user.organization_id,
                entity_category="educational_institution",
                entity_type="institution",
                education_entity_type="College",
            )
    if user.organization_id:
        existing_org = await OrganizationRepository.get_by_id(session, user.organization_id)
        if existing_org is None:
            await OrganizationRepository.upsert(
                session=session,
                org_id=user.organization_id,
                name=user.organization_id,
                owner_user_id=user.id,
            )

    if not user.is_active:
        raise credentials_exception
    return user


def require_role(*allowed_roles: str):
    """
    Dependency factory checking whether the authenticated user has one of the allowed roles.
    Supported roles: SuperAdmin, Auditor, Analyst, Viewer.
    SuperAdmin is always granted access.
    """
    async def role_checker(current_user: UserModel = Depends(get_current_user)) -> UserModel:
        if current_user.role == "SuperAdmin" or current_user.role in allowed_roles:
            return current_user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: requires one of {allowed_roles}, your role is '{current_user.role}'"
        )
    return role_checker

