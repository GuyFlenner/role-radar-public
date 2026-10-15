"""Gmail API message -> plain-text extraction.

Deterministic, zero-LLM-call, stdlib only. Walks a Gmail API `messages.get` resource's
`payload` (multipart MIME, base64url-encoded per Gmail's own API — RFC 4648 "URL and
filename safe" alphabet, not the standard base64 alphabet), preferring `text/plain` over
`text/html`. See docs/superpowers/plans/2026-08-20-headhunter-email-intake-plan.md (Task 2).
"""

from __future__ import annotations

import base64
import re
from html.parser import HTMLParser

_SCRIPT_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        self._chunks.append(data)

    def text(self) -> str:
        return "".join(self._chunks)


def _decode_base64url(data: str) -> str:
    # Gmail pads inconsistently; add back the padding base64 needs before decoding.
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def _html_to_text(html: str) -> str:
    stripped = _SCRIPT_STYLE.sub("", html)
    parser = _TextExtractor()
    parser.feed(stripped)
    text = parser.text()
    # Collapse the whitespace HTML tags leave behind without losing paragraph breaks entirely.
    return re.sub(r"[ \t]+", " ", text).strip()


def _walk_parts(payload: dict) -> list[dict]:
    """Flatten a (possibly nested) multipart payload into a list of leaf parts."""
    parts = payload.get("parts")
    if not parts:
        return [payload]
    leaves: list[dict] = []
    for part in parts:
        leaves.extend(_walk_parts(part))
    return leaves


def extract_text(message: dict) -> str:
    """Extract the best available plain text from a Gmail API message resource.

    Prefers `text/plain`; falls back to stripping tags from `text/html` when no plain part
    exists (a common shape for HTML-only recruiter emails from an ATS/agency template).
    Returns "" if neither is present rather than raising — a malformed/empty message is a
    data-quality signal for the caller (e.g. flag NEEDS_HUMAN), not a crash.
    """
    payload = message.get("payload") or {}
    leaves = _walk_parts(payload)

    plain_texts: list[str] = []
    html_texts: list[str] = []
    for leaf in leaves:
        mime_type = leaf.get("mimeType", "")
        data = (leaf.get("body") or {}).get("data")
        if not data:
            continue
        decoded = _decode_base64url(data)
        if mime_type == "text/plain":
            plain_texts.append(decoded)
        elif mime_type == "text/html":
            html_texts.append(decoded)

    if plain_texts:
        return "\n".join(plain_texts).strip()
    if html_texts:
        return "\n".join(_html_to_text(h) for h in html_texts).strip()
    return ""
