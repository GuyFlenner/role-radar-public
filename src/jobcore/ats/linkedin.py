"""LinkedIn public guest job-search provider.

Reaches employers that don't publish on Greenhouse/Comeet — notably the defense
primes (Rafael, Elbit) — via the PUBLIC, unauthenticated guest search endpoint:

    GET https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search
        ?f_C={company_id}&location={location}&start=0

Design constraints (same stance as ``liveness.py``):
- **Unauthenticated only.** Guest endpoint, never a logged-in session — driving
  LinkedIn with your cookies violates their ToS and risks your real account.
- **A block is not "no jobs we care about".** 403/429/999 are carried as a *status*
  by ``http_get_text_status``, retried with backoff, and — if they persist — reported
  as an explicit truncation. This used to be aspiration rather than fact: the body-only
  getter mapped a block to "", and the pager's ``if not jobs: break`` read that as
  "no more pages", so a throttled sweep reported as a complete one. That is the
  2026-08-03 Elbit defect; ``_get_page`` exists to keep the two apart.

`target` shape in ats_targets.json::

    {"company": "Elbit Systems", "ats": "linkedin", "company_id": "<f_C>",
     "location": "<country>", "commute_extra": ["<extra city>"]}
"""

from __future__ import annotations

import random
import re
import sys
import time
from urllib.parse import quote

from .base import BLOCK_STATUSES, http_get_text_status, iso_date, make_raw, strip_html

SEARCH_URL = (
    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    "?f_C={company_id}&keywords={keywords}&location={location}&start={start}"
)
# Company-agnostic market search — same guest endpoint, NO f_C filter. Returns
# roles from ALL companies matching keywords+location, so a great role at a company
# not in ats_targets.json (e.g. Cellxpert) still gets caught.
MARKET_URL = (
    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    "?keywords={keywords}&location={location}&start={start}"
)

_PAGE_STEP = 10  # guest endpoint returns ~10 cards/page
_MAX_PAGES = 3  # 3 pages ≈ 30 most-recent per keyword — enough for the delta poll

# Throttling. The guest endpoint starts returning 429 partway through a multi-keyword
# run (observed 2026-08-03: Elbit lost 4 keyword pages, plus Sympera/Faye/LinearB/
# Varonis). Two mitigations, because one is not enough:
#   - a short randomised pause between pages, to avoid tripping the limit at all;
#   - retry-with-backoff when we trip it anyway.
_MAX_RETRIES = 3  # attempts per page, not extra attempts
_BASE_BACKOFF_SEC = 2.0  # doubles per attempt: 2s, 4s
_PAGE_PAUSE_SEC = 0.4  # base inter-page pause; jitter is added on top


def _call_getter(getter, url: str) -> tuple[str, int]:
    """Normalise a getter to ``(body, status)``, accepting either shape.

    Production passes ``http_get_text_status``. Tests and older callers pass a
    body-only callable; those get a synthetic status (200 with a body, 0 without),
    which never looks like a block, so their behaviour is exactly as before.
    """
    res = getter(url)
    if isinstance(res, tuple) and len(res) == 2:
        body, status = res
        return str(body or ""), int(status or 0)
    body = str(res or "")
    return body, (200 if body else 0)


def _get_page(getter, url: str, sleep, rand, max_retries: int) -> tuple[str, bool]:
    """Fetch one page → ``(body, blocked)``. Retries block statuses with backoff.

    ``blocked`` is the whole point: it separates "the host refused us" from "there
    are no more results", which an empty body alone cannot express.
    """
    for attempt in range(max_retries):
        body, status = _call_getter(getter, url)
        if status not in BLOCK_STATUSES:
            return body, False
        if attempt == max_retries - 1:
            break
        # Exponential backoff with jitter, so parallel/repeated runs don't
        # synchronise their retries into another burst.
        delay = _BASE_BACKOFF_SEC * (2**attempt) * (1.0 + rand())
        print(
            f"  linkedin: HTTP {status} — backing off {delay:.1f}s (attempt {attempt + 1}/{max_retries})",
            file=sys.stderr,
        )
        sleep(delay)
    return "", True


def _note_truncation(report, label: str, kw: str, page: int) -> None:
    """Record + announce that a keyword's paging was cut short by a block.

    Never silent: a truncated sweep that reports like a complete one is how Elbit
    looked fully scanned while four keyword pages were missing.
    """
    print(
        f"  linkedin: ⚠ {label} keyword {kw!r} TRUNCATED at page {page + 1} "
        f"(rate-limited, not exhausted) — results are incomplete",
        file=sys.stderr,
    )
    if report is not None:
        report.append({"target": label, "keyword": kw, "page": page + 1, "reason": "rate_limited"})


# One job card. LinkedIn wraps each in a base-card div carrying the posting urn.
_CARD_RE = re.compile(r"urn:li:jobPosting:(\d{6,})(.*?)(?=urn:li:jobPosting:|\Z)", re.S)
_TITLE_RE = re.compile(r'base-search-card__title"[^>]*>(.*?)</', re.S)
_COMPANY_RE = re.compile(r'base-search-card__subtitle"[^>]*>(.*?)</', re.S)
_LOCATION_RE = re.compile(r'job-search-card__location"[^>]*>(.*?)</', re.S)
_HREF_RE = re.compile(r'base-card__full-link"[^>]*href="([^"]+)"', re.S)
_DATE_RE = re.compile(r'datetime="([^"]+)"')


def parse(company: str, html: str | None) -> list[dict]:
    """Pure: LinkedIn guest job-search HTML → raw job dicts. Defensive to markup."""
    if not html:
        return []
    out: list[dict] = []
    for m in _CARD_RE.finditer(html):
        job_id, block = m.group(1), m.group(2)
        tm = _TITLE_RE.search(block)
        title = strip_html(tm.group(1)) if tm else ""
        if not title:
            continue
        # The card's own company wins; the `company` arg is the fallback (blank in
        # market mode, the target name in targeted mode).
        cm = _COMPANY_RE.search(block)
        card_company = strip_html(cm.group(1)) if cm else ""
        co = card_company or company
        lm = _LOCATION_RE.search(block)
        location = strip_html(lm.group(1)) if lm else ""
        hm = _HREF_RE.search(block)
        url = hm.group(1).split("?")[0] if hm else ""  # drop tracking query params
        if not url:  # some cards omit the link — reconstruct the canonical job URL
            url = f"https://www.linkedin.com/jobs/view/{job_id}"
        dm = _DATE_RE.search(block)
        posted = iso_date(dm.group(1)) if dm else None
        out.append(
            make_raw(
                company=co,
                title=title,
                url=url,
                source=f"linkedin:{co}",
                location=location,
                snippet="",  # guest search cards carry no description; JD fetched later if needed
                posted_date=posted,
                job_id=job_id,
            )
        )
    return out


def fetch(
    target: dict,
    get_text=http_get_text_status,
    max_pages: int = _MAX_PAGES,
    *,
    sleep=time.sleep,
    rand=random.random,
    report: list | None = None,
    max_retries: int = _MAX_RETRIES,
) -> list[dict]:
    """Fetch one company's LinkedIn guest listing. `target` needs {company, company_id}.

    Big employers (defense primes) post far more than one page of newest roles, so
    a bare fetch surfaces welders and program managers, not the software-leadership
    roles we want. Two levers make it useful:

    - ``keywords`` (str or list): each phrase is sent to the guest search's
      ``keywords=`` filter, so the board returns roles matching e.g. "software team
      lead" rather than the 10 most-recent of anything. Omit → one unfiltered query.
    - pagination: each keyword is paged (``start`` += 10) up to ``max_pages`` or the
      first empty page. Results are deduped by ``job_id`` across keywords and pages.

    A rate-limited page is **not** treated as the end of the keyword. It is retried
    with backoff, and if it stays blocked the truncation is announced on stderr and
    appended to ``report`` (when given) so the caller knows the sweep is incomplete.
    """
    company_id = target.get("company_id")
    if not company_id:
        return []
    location = quote(str(target.get("location", "")))
    company = target.get("company", str(company_id))
    keywords = target.get("keywords") or [""]
    if isinstance(keywords, str):
        keywords = [keywords]

    deduped: dict[str, dict] = {}
    for kw in keywords:
        for page in range(max_pages):
            url = SEARCH_URL.format(
                company_id=quote(str(company_id)),
                keywords=quote(str(kw)),
                location=location,
                start=page * _PAGE_STEP,
            )
            if page:  # be a good guest — pause before every page after the first
                sleep(_PAGE_PAUSE_SEC * (1.0 + rand()))
            body, blocked = _get_page(get_text, url, sleep, rand, max_retries)
            if blocked:
                _note_truncation(report, company, str(kw), page)
                break
            jobs = parse(company, body)
            if not jobs:
                break  # genuinely no more pages for this keyword
            for j in jobs:
                deduped.setdefault(j["job_id"] or j["url"] or j["title"], j)
    return list(deduped.values())


def search_market(
    keywords,
    location: str = "",
    get_text=http_get_text_status,
    max_pages: int = _MAX_PAGES,
    *,
    sleep=time.sleep,
    rand=random.random,
    report: list | None = None,
    max_retries: int = _MAX_RETRIES,
) -> list[dict]:
    """Company-AGNOSTIC market search — the discovery gap the targeted poll can't cover.

    Hits the guest endpoint with keywords+location and NO ``f_C`` filter, so it
    returns matching roles from every company (each card's real employer is read
    from the card). Feeds the same relevance/liveness/scoring core. Deduped by
    ``job_id`` across keywords/pages. This is what would have caught e.g. Cellxpert
    (a company not in ats_targets.json).
    """
    if isinstance(keywords, str):
        keywords = [keywords]
    loc = quote(str(location))
    deduped: dict[str, dict] = {}
    for kw in keywords:
        for page in range(max_pages):
            url = MARKET_URL.format(keywords=quote(str(kw)), location=loc, start=page * _PAGE_STEP)
            if page:
                sleep(_PAGE_PAUSE_SEC * (1.0 + rand()))
            body, blocked = _get_page(get_text, url, sleep, rand, max_retries)
            if blocked:
                _note_truncation(report, "market", str(kw), page)
                break
            jobs = parse("", body)  # blank company → per-card employer
            if not jobs:
                break
            for j in jobs:
                deduped.setdefault(j["job_id"] or j["url"] or j["title"], j)
    return list(deduped.values())
