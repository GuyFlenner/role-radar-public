"""Shared helpers for ATS providers — HTTP, date/HTML normalization, raw-job builder.

stdlib-only (urllib) to match the rest of the deterministic core and the
liveness util. No new dependency.
"""

from __future__ import annotations

import html as _html
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime

from ..net import require_http

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
_TIMEOUT_SEC = 20
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def http_get_json(url: str):
    """GET a URL and parse JSON. Returns the decoded object, or None on any failure.

    Deliberately swallows network/parse errors and returns None so one dead board
    never aborts a multi-company poll; the caller logs and skips.
    """
    req = urllib.request.Request(require_http(url), headers={"User-Agent": _UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SEC) as resp:
            if resp.getcode() != 200:
                return None
            return json.loads(resp.read().decode("utf-8", errors="ignore"))
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as e:
        print(f"  ats: fetch failed for {url}: {str(e)[:120]}", file=sys.stderr)
        return None


# Statuses that mean "the host refused us", not "there is nothing here".
# 999 is LinkedIn's own anti-bot code; 403/429 are the generic block/throttle pair.
BLOCK_STATUSES = frozenset({403, 429, 999})


def http_get_text_status(url: str) -> tuple[str, int]:
    """GET a URL → ``(body, status)``. ``status`` is 0 on a network failure/timeout.

    The status matters because an empty body is *ambiguous*: it is returned both for
    "this page has no results" and for "you are being throttled". Callers that page
    through results must tell those apart — treating a 429 as an empty page silently
    truncates the run and looks like a complete one. See ``linkedin.fetch``.
    """
    req = urllib.request.Request(require_http(url), headers={"User-Agent": _UA, "Accept-Language": "en"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SEC) as resp:
            code = resp.getcode()
            if code != 200:
                return "", code
            return resp.read().decode("utf-8", errors="ignore"), code
    except urllib.error.HTTPError as e:  # carries the refusal code — keep it
        print(f"  ats: HTTP {e.code} for {url}", file=sys.stderr)
        return "", int(e.code)
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        print(f"  ats: fetch failed for {url}: {str(e)[:120]}", file=sys.stderr)
        return "", 0


def http_get_text(url: str) -> str:
    """GET a URL and return the decoded body as text, or "" on any failure/block.

    HTML sibling of ``http_get_json`` for providers that scrape a public guest
    page (LinkedIn). Swallows network errors AND anti-bot blocks (403/429/999) so
    one blocked board never aborts the poll; the caller treats "" as "no jobs".

    Kept as the simple body-only form for callers that genuinely cannot act on a
    status. Anything that paginates should use ``http_get_text_status``.
    """
    return http_get_text_status(url)[0]


def iso_date(value) -> date | None:
    """Best-effort parse of an ATS timestamp into a date. None if unparseable.

    Handles ISO-8601 strings (with/without time and Z), plain YYYY-MM-DD, and
    numeric unix timestamps. None is safe downstream — it maps to recency
    'Unknown' and the job is kept, never wrongly stale-dropped.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value)).date()
        except (OSError, ValueError, OverflowError):
            return None
    s = str(value).strip()
    if not s:
        return None
    iso = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(iso).date()
    except ValueError:
        pass
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            return None
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a %b %d %Y", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def strip_html(text: str, limit: int = 500) -> str:
    """Unescape entities, drop tags, collapse whitespace, truncate."""
    if not text:
        return ""
    plain = _TAG_RE.sub(" ", _html.unescape(text))
    return _WS_RE.sub(" ", plain).strip()[:limit]


def make_raw(
    *,
    company: str,
    title: str,
    url: str,
    source: str,
    location: str = "",
    domain: str = "",
    snippet: str = "",
    posted_date: date | None = None,
    job_id: str = "",
) -> dict:
    """Assemble the raw job dict in the exact shape filter_score_positions expects.

    ``job_id`` is the provider's stable posting id (Greenhouse job id, Comeet
    position uid, LinkedIn job-id). It becomes the preferred delta key so a
    re-titled req isn't re-surfaced and a re-opened one isn't missed; empty is
    fine and falls back to the (title, company) key.
    """
    return {
        "title": (title or "").strip(),
        "company": (company or "").strip(),
        "location": (location or "").strip(),
        "domain": (domain or "").strip(),
        "url": (url or "").strip(),
        "snippet": (snippet or "").strip(),
        "posted_date": posted_date,
        "posted_str": posted_date.strftime("%Y-%m-%d") if posted_date else "",
        "source": source,
        "job_id": (job_id or "").strip(),
    }
