"""
Security, SSRF Protection, Untrusted Content Sanitization, and Ethical OSINT Boundary Enforcement.
Complies with Sections 23 & 24 of the AI-CRISS OSINT & Global Intelligence Specification.
"""

import hashlib
import html
import ipaddress
import re
import socket
from urllib.parse import urlparse
import xml.etree.ElementTree as ET


class SSRFViolationError(ValueError):
    """Raised when a URL targets a forbidden scheme, loopback, metadata endpoint, or private network."""


class UnethicalOSINTRequestError(ValueError):
    """Raised when a request violates lawful/ethical public-source OSINT boundaries."""


FORBIDDEN_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata",
    "169.254.169.254",
    "0.0.0.0",
    "[::1]",
    "::1",
}

FORBIDDEN_ETHICAL_PATTERNS = [
    r"\b(steal|dump|crack|bruteforce|brute-force)\s+(password|credential|session|cookie|token)s?\b",
    r"\b(password|credential|session|cookie|token)s?\s+(dump|theft|stealing|cracking|bruteforce)\b",
    r"\b(deploy|inject|upload)\s+(malware|ransomware|backdoor|shellcode|trojan)\b",
    r"\bbypass\s+(authentication|2fa|mfa|login|paywall)\b",
    r"\bunauthorized\s+(account\s+access|intrusion|exploitation)\b",
]


def _check_ip_address_safe(ip_str: str) -> None:
    """Raise SSRFViolationError if ip_str is private, loopback, link-local, multicast, or reserved."""
    try:
        ip_obj = ipaddress.ip_address(ip_str)
    except ValueError as exc:
        raise SSRFViolationError(f"Invalid IP address format '{ip_str}': {exc}") from exc

    if (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
    ):
        raise SSRFViolationError(
            f"SSRF_BLOCKED: Target IP '{ip_str}' resolves to a restricted/internal network range."
        )


def validate_external_url(url: str, resolve_dns: bool = False) -> str:
    """
    Validate that a URL is safe for outbound OSINT collection.
    Blocks non-HTTP(S) schemes, credentials in URLs, localhost, cloud metadata endpoints,
    and RFC1918 / loopback / link-local IP addresses.
    """
    if not url or not isinstance(url, str):
        raise SSRFViolationError("SSRF_BLOCKED: URL must be a non-empty string.")

    cleaned = url.strip()
    parsed = urlparse(cleaned)

    if parsed.scheme.lower() not in ("http", "https"):
        raise SSRFViolationError(
            f"SSRF_BLOCKED: Disallowed URL scheme '{parsed.scheme}'. Only 'http' and 'https' are permitted."
        )

    if parsed.username or parsed.password:
        raise SSRFViolationError("SSRF_BLOCKED: Embedded credentials in URLs are forbidden.")

    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        raise SSRFViolationError("SSRF_BLOCKED: URL is missing a valid hostname.")

    if (
        hostname in FORBIDDEN_HOSTNAMES
        or hostname.endswith(".local")
        or hostname.endswith(".internal")
        or hostname.endswith(".localhost")
    ):
        raise SSRFViolationError(f"SSRF_BLOCKED: Hostname '{hostname}' is restricted.")

    # Check if hostname is a literal IP address
    try:
        ipaddress.ip_address(hostname)
        is_literal_ip = True
    except ValueError:
        is_literal_ip = False

    if is_literal_ip:
        _check_ip_address_safe(hostname)
    elif resolve_dns:
        try:
            addr_info = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
            for family, _, _, _, sockaddr in addr_info:
                resolved_ip = sockaddr[0]
                _check_ip_address_safe(resolved_ip)
        except SSRFViolationError:
            raise
        except socket.gaierror:
            # If offline or unresolvable in test environment, syntactic & literal checks still pass
            pass

    return cleaned


def sanitize_untrusted_text(raw_text: str, max_length: int = 20000) -> str:
    """
    Treat externally retrieved web/feed content as untrusted data.
    Removes active script/iframe/object blocks, inline event handlers, and control bytes.
    """
    if not raw_text:
        return ""
    text = str(raw_text)
    # Remove <script>, <style>, <iframe>, <object>, <embed> blocks completely
    text = re.sub(
        r"<(script|style|iframe|object|embed|applet|meta|link)\b[^>]*>.*?</\1>",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    # Remove self-closing or unclosed dangerous tags
    text = re.sub(
        r"<(script|style|iframe|object|embed|applet|meta|link)\b[^>]*>",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    # Strip remaining HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    # Remove non-printable control characters except newline/tab
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # Collapse excessive whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_length]


def safe_parse_xml(xml_text: str) -> ET.Element:
    """
    Parse RSS/Atom XML safely, rejecting XXE / DOCTYPE / ENTITY declarations.
    """
    if not xml_text or not isinstance(xml_text, str):
        raise ValueError("Empty XML payload.")
    if re.search(r"<!\s*(DOCTYPE|ENTITY)\b", xml_text, flags=re.IGNORECASE):
        raise ValueError("SECURITY_BLOCKED: XML payload contains forbidden DOCTYPE or ENTITY declaration (XXE protection).")
    return ET.fromstring(xml_text.strip())


def compute_content_hash(content: str) -> str:
    """Compute canonical SHA-256 hex digest of normalized content."""
    normalized = re.sub(r"\s+", " ", (content or "").strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def enforce_lawful_osint_query(query_or_target: str) -> None:
    """
    Enforce Section 24 Legal & Ethical OSINT Boundaries.
    Blocks requests attempting credential theft, unauthorized access, malware deployment, or authentication bypass.
    """
    if not query_or_target:
        return
    for pattern in FORBIDDEN_ETHICAL_PATTERNS:
        if re.search(pattern, query_or_target, flags=re.IGNORECASE):
            raise UnethicalOSINTRequestError(
                "LEGAL_ETHICAL_BOUNDARY_VIOLATION: Request violates AI-CRISS lawful public-source OSINT policy."
            )
