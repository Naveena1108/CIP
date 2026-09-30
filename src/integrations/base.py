"""
Internal Abstraction Layer, TTL/ETag/Content-Hash Cache, Observability, and Failure Isolation
for AI-CRISS OSINT & Global Intelligence Providers (Sections 2, 25, 26, 27).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.contracts.osint_intelligence import (
    OSINTSource,
    OSINTEvidence,
    OSINTEntity,
    OSINTEvent,
    OSINTRelationship,
    ProviderHealthState,
    ProviderObservabilityItem,
)
from src.engine.llm_reasoner import redact_secrets
from src.security.ssrf_guard import compute_content_hash

logger = logging.getLogger("ai_criss.osint.integrations")


@dataclass
class CachedEntry:
    key: str
    value: Any
    content_hash: str
    etag: Optional[str]
    stored_at_monotonic: float
    ttl_seconds: float


class OSINTResponseCache:
    """
    In-memory TTL, ETag, and SHA-256 content-hash cache for OSINT providers (Section 27).
    Prevents redundant external requests and supports incremental deduplicated ingestion.
    """

    def __init__(self, default_ttl_seconds: float = 300.0, max_entries: int = 1024):
        self.default_ttl_seconds = default_ttl_seconds
        self.max_entries = max_entries
        self._store: Dict[str, CachedEntry] = {}
        self._seen_content_hashes: Dict[str, float] = {}
        self.hits: int = 0
        self.misses: int = 0
        self.etag_revalidations: int = 0
        self.incremental_skips: int = 0

    @staticmethod
    def make_key(provider_id: str, operation: str, params: Dict[str, Any]) -> str:
        serialized = json.dumps(params, sort_keys=True, default=str)
        digest = hashlib.sha256(f"{provider_id}:{operation}:{serialized}".encode("utf-8")).hexdigest()
        return f"{provider_id}:{operation}:{digest[:24]}"

    def get(self, key: str, current_etag: Optional[str] = None) -> Optional[Any]:
        now = time.monotonic()
        entry = self._store.get(key)
        if not entry:
            self.misses += 1
            return None

        if current_etag and entry.etag and current_etag == entry.etag:
            entry.stored_at_monotonic = now
            self.hits += 1
            self.etag_revalidations += 1
            return entry.value

        if (now - entry.stored_at_monotonic) > entry.ttl_seconds:
            del self._store[key]
            self.misses += 1
            return None

        self.hits += 1
        return entry.value

    def set(
        self,
        key: str,
        value: Any,
        ttl_seconds: Optional[float] = None,
        etag: Optional[str] = None,
        raw_text_for_hash: Optional[str] = None,
    ) -> str:
        if len(self._store) >= self.max_entries:
            oldest_key = min(self._store.keys(), key=lambda k: self._store[k].stored_at_monotonic)
            del self._store[oldest_key]

        c_hash = compute_content_hash(raw_text_for_hash or json.dumps(value, default=str))
        now = time.monotonic()
        self._store[key] = CachedEntry(
            key=key,
            value=value,
            content_hash=c_hash,
            etag=etag,
            stored_at_monotonic=now,
            ttl_seconds=ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds,
        )
        self._seen_content_hashes[c_hash] = now
        return c_hash

    def is_duplicate_content(self, content: str, window_seconds: float = 3600.0) -> Tuple[bool, str]:
        """Check whether identical content hash was already ingested within the window."""
        c_hash = compute_content_hash(content)
        now = time.monotonic()
        seen_at = self._seen_content_hashes.get(c_hash)
        if seen_at is not None and (now - seen_at) <= window_seconds:
            self.incremental_skips += 1
            return True, c_hash
        self._seen_content_hashes[c_hash] = now
        return False, c_hash

    def stats(self) -> Dict[str, Any]:
        return {
            "active_entries": len(self._store),
            "seen_content_hashes": len(self._seen_content_hashes),
            "hits": self.hits,
            "misses": self.misses,
            "etag_revalidations": self.etag_revalidations,
            "incremental_skips": self.incremental_skips,
        }


GLOBAL_OSINT_CACHE = OSINTResponseCache(default_ttl_seconds=300.0)


@dataclass
class ProviderIngestionBundle:
    """Normalized output returned by any OSINT provider adapter."""
    provider_id: str
    sources: List[OSINTSource] = field(default_factory=list)
    evidence: List[OSINTEvidence] = field(default_factory=list)
    entities: List[OSINTEntity] = field(default_factory=list)
    events: List[OSINTEvent] = field(default_factory=list)
    relationships: List[OSINTRelationship] = field(default_factory=list)
    used_fallback_or_cache: bool = False
    error_message: Optional[str] = None


class BaseOSINTProvider(ABC):
    """
    Abstract base class for all AI-CRISS OSINT & Threat Intelligence providers.
    Enforces:
    - Provider replacement without rewriting the intelligence pipeline (Section 2)
    - Failure isolation so a down external service never breaks AI-CRISS (Section 26)
    - Structured logging, latency/error/request/ingestion counters, and health checks (Section 25)
    """

    provider_id: str = "base_provider"
    display_name: str = "Base OSINT Provider"
    priority: int = 99
    capabilities: List[str] = []

    def __init__(self, cache: Optional[OSINTResponseCache] = None):
        self.cache = cache or GLOBAL_OSINT_CACHE
        self.request_count: int = 0
        self.ingestion_count: int = 0
        self.error_count: int = 0
        self.failed_jobs_count: int = 0
        self._total_latency_ms: float = 0.0
        self.last_error: Optional[str] = None
        self.last_checked_at: datetime = datetime.now(timezone.utc)
        self._forced_degraded: bool = False

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if live external credentials/endpoints are configured."""

    @abstractmethod
    def collect_intelligence(self, query: Optional[str] = None, **kwargs: Any) -> ProviderIngestionBundle:
        """Collect and normalize intelligence into a ProviderIngestionBundle."""

    def execute_fault_isolated(
        self,
        operation_name: str,
        primary_fn: Callable[[], ProviderIngestionBundle],
        fallback_fn: Optional[Callable[[str], ProviderIngestionBundle]] = None,
        secrets_to_redact: Optional[List[str]] = None,
    ) -> ProviderIngestionBundle:
        """
        Execute a provider operation inside a strict fault-isolation boundary.
        If the external provider raises an exception or times out, records metrics,
        redacts any secrets from logs, and invokes `fallback_fn` or returns an empty bundle.
        """
        self.request_count += 1
        start = time.perf_counter()
        self.last_checked_at = datetime.now(timezone.utc)
        try:
            bundle = primary_fn()
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            self._total_latency_ms += elapsed_ms
            self.ingestion_count += len(bundle.events) + len(bundle.entities) + len(bundle.evidence)
            if not bundle.error_message:
                self.last_error = None
            return bundle
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            self._total_latency_ms += elapsed_ms
            self.error_count += 1
            self.failed_jobs_count += 1
            safe_err = redact_secrets(f"{type(exc).__name__}: {exc}", secrets_to_redact or [])
            self.last_error = safe_err
            logger.warning(
                "OSINT provider '%s' failed during '%s' (%0.2f ms): %s — isolating failure.",
                self.provider_id,
                operation_name,
                elapsed_ms,
                safe_err,
            )
            if fallback_fn is not None:
                fb_bundle = fallback_fn(safe_err)
                fb_bundle.used_fallback_or_cache = True
                fb_bundle.error_message = safe_err
                self.ingestion_count += len(fb_bundle.events) + len(fb_bundle.entities) + len(fb_bundle.evidence)
                return fb_bundle
            return ProviderIngestionBundle(
                provider_id=self.provider_id,
                used_fallback_or_cache=True,
                error_message=safe_err,
            )

    def health_check(self) -> ProviderObservabilityItem:
        """Return structured health and observability status for this provider."""
        self.last_checked_at = datetime.now(timezone.utc)
        configured = self.is_configured()
        avg_lat = round(self._total_latency_ms / self.request_count, 2) if self.request_count > 0 else 0.0

        if self._forced_degraded or self.last_error:
            state: ProviderHealthState = "DEGRADED"
            live_ok = False
            fallback_active = True
        elif configured:
            state = "HEALTHY"
            live_ok = True
            fallback_active = False
        else:
            # Operational via deterministic reference adapter when external endpoint is unconfigured
            state = "HEALTHY"
            live_ok = False
            fallback_active = True

        return ProviderObservabilityItem(
            provider_id=self.provider_id,
            display_name=self.display_name,
            priority=self.priority,
            status=state,
            configured=configured,
            live_endpoint_reachable=live_ok,
            fallback_mode_active=fallback_active,
            request_count=self.request_count,
            ingestion_count=self.ingestion_count,
            error_count=self.error_count,
            failed_jobs_count=self.failed_jobs_count,
            avg_latency_ms=avg_lat,
            last_checked_at=self.last_checked_at,
            last_error=self.last_error,
            capabilities=list(self.capabilities),
        )
