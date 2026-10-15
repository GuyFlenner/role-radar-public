"""Tiny shared network helpers (stdlib only)."""

from __future__ import annotations

from urllib.parse import urlparse


def require_http(url: str) -> str:
    """Refuse any URL scheme other than http/https (``urlopen`` would happily open ``file://``)."""
    if urlparse(url).scheme.lower() not in ("http", "https"):
        raise ValueError(f"refusing non-http(s) URL: {url!r}")
    return url
