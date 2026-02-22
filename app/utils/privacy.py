"""
LifeOS – Privacy Filter Utility
Ensures no full URLs, search queries, or sensitive data is ever stored.
"""

import re
from urllib.parse import urlparse


# Domains where we should NEVER log anything beyond the domain itself
SENSITIVE_DOMAINS = {
    "mail.google.com", "outlook.live.com", "web.whatsapp.com",
    "messages.google.com", "discord.com", "slack.com",
}

# Regex to validate sanitized hostname format
HOSTNAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-\.]*\.[a-zA-Z]{2,}$")


def sanitize_url(raw_url: str) -> str:
    """
    Extract hostname only. Strip query params, paths, fragments.
    Returns empty string if URL is invalid.
    """
    try:
        parsed = urlparse(raw_url)
        hostname = parsed.hostname or ""
        hostname = hostname.lower().strip()

        # Remove www. prefix for consistency
        if hostname.startswith("www."):
            hostname = hostname[4:]

        # Validate format
        if not HOSTNAME_PATTERN.match(hostname):
            return ""

        return hostname
    except Exception:
        return ""


def is_sensitive_domain(domain: str) -> bool:
    """Check if domain should have extra privacy protection."""
    return domain in SENSITIVE_DOMAINS


def sanitize_tracking_data(data: dict) -> dict:
    """
    Clean tracking data before storage.
    Ensures no full URLs, query params, or sensitive info.
    """
    cleaned = {}

    if "url" in data:
        cleaned["domain"] = sanitize_url(data["url"])
    elif "domain" in data:
        cleaned["domain"] = sanitize_url(f"https://{data['domain']}")

    # Copy allowed fields
    allowed_fields = {
        "duration_seconds", "tab_switches", "scroll_depth",
        "is_active", "timestamp"
    }
    for field in allowed_fields:
        if field in data:
            cleaned[field] = data[field]

    return cleaned
