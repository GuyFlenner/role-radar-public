"""Pre-outreach job-posting liveness check.

Root cause this closes: outreach referenced a role whose public posting had
already closed ("No longer accepting applications"), because liveness was read
off an aggregator (DevJobs) and never confirmed on the canonical source before
sending. This util is the automatable half of the gate — run it on the *apply*
URL before naming a specific role in any outreach.

Design constraints (deliberate):
- **Unauthenticated only.** For LinkedIn we hit the PUBLIC guest endpoint
  (``/jobs-guest/jobs/api/jobPosting/{id}``) — never a logged-in session. Driving
  LinkedIn with your own cookies violates their ToS and risks your real account.
- **A block is not a "closed".** LinkedIn answers scrapers with 403/429/999. The
  archived ``check_jd_links.py`` wrongly mapped 403 → closed; here a block →
  UNVERIFIED ("verify in browser"), so we never talk ourselves out of a live role.
- **Best-effort, not authoritative.** Guest endpoints don't always surface the
  closed banner → expect UNVERIFIED for some closed LinkedIn jobs. Trust CLOSED;
  treat UNVERIFIED as "open the link and look." The human click stays the backstop.

CLI::

    uv run python -m src.jobcore.liveness <url> [<url> ...]

Exit code: 0 = all OPEN, 3 = any CLOSED, 2 = any UNVERIFIED (none closed).
"""

from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime

from .console import force_utf8_output
from .gates import classify_liveness

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
_TIMEOUT_SEC = 15
# LinkedIn/anti-bot statuses: a block, NOT evidence the posting is closed.
_BLOCK_CODES = frozenset({403, 429, 999})
# Job id in a LinkedIn URL: trailing digits of /jobs/view/... or ?currentJobId=.
_LI_VIEW_ID = re.compile(r"/jobs/view/(?:[^/?#]*?-)?(\d{6,})")
_LI_QUERY_ID = re.compile(r"[?&]currentJobId=(\d{6,})")

# Third-party AGGREGATOR / mirror hosts. They cache postings and keep stale "apply"
# buttons live after the employer has closed the role, so an OPEN read here is NOT
# authoritative — check_url downgrades it to UNVERIFIED (a CLOSED read is still trusted).
# Canonical sources (LinkedIn guest, Greenhouse/Comeet/Lever ATS, the employer's own
# careers/apply page) are deliberately NOT listed and keep their OPEN.
# Root cause: DevJobs showed OPEN while LinkedIn said "no longer accepting applications"
# — 2026-07-08 and again on Elbit req 4375556725, 2026-07-24.
_AGGREGATOR_HOSTS = (
    "devjobs.co.il",
    "jobify360.co.il",
    "jsjobs.co.il",
    "secrettelaviv.com",
    "trabajo.org",
    "themuse.com",
    "glassdoor.",
    "startup.jobs",
    "builtin.com",
    "jobdex.io",
    "pitchmeai.com",
    "jaabz.com",
)


@dataclass
class FetchResult:
    http: int | None
    text: str
    blocked: bool = False
    error: str | None = None


@dataclass
class LivenessResult:
    url: str
    resolved_url: str
    source: str  # 'linkedin-guest' | 'aggregator' | 'direct'
    status: str  # 'OPEN' | 'CLOSED' | 'UNVERIFIED'
    signal: str
    http: int | None
    checked_at: str

    def line(self) -> str:
        return (
            f"{self.status:<11} {self.signal}\n"
            f"            source: {self.source} | http: {self.http} | checked: {self.checked_at}\n"
            f"            {self.url}"
        )


def linkedin_job_id(url: str) -> str | None:
    """Extract the numeric LinkedIn job id, or None if not a LinkedIn job URL."""
    if "linkedin.com" not in url.lower():
        return None
    m = _LI_VIEW_ID.search(url) or _LI_QUERY_ID.search(url)
    return m.group(1) if m else None


def is_aggregator(url: str) -> bool:
    """True for third-party job-board mirrors whose OPEN signal is not authoritative."""
    u = url.lower()
    return any(host in u for host in _AGGREGATOR_HOSTS)


def resolve(url: str) -> tuple[str, str]:
    """Map a URL to (fetch_url, source). LinkedIn job → public guest endpoint;
    known aggregator/mirror hosts → 'aggregator' (OPEN there is not authoritative)."""
    job_id = linkedin_job_id(url)
    if job_id:
        return (
            f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}",
            "linkedin-guest",
        )
    if is_aggregator(url):
        return url, "aggregator"
    return url, "direct"


def _fetch(url: str) -> FetchResult:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept-Language": "en"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SEC) as resp:
            code = resp.getcode()
            body = resp.read().decode("utf-8", errors="ignore")
        return FetchResult(http=code, text=body)
    except urllib.error.HTTPError as e:
        if e.code in _BLOCK_CODES:
            return FetchResult(http=e.code, text="", blocked=True)
        # 404/410 etc.: keep the code, no body needed.
        return FetchResult(http=e.code, text="")
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        return FetchResult(http=None, text="", error=str(e)[:120])


def check_url(url: str, fetch=_fetch) -> LivenessResult:
    """Resolve, fetch, and classify one URL. ``fetch`` is injectable for tests."""
    resolved, source = resolve(url)
    fr = fetch(resolved)
    now = datetime.now(UTC).isoformat(timespec="seconds")

    if fr.blocked:
        status, signal = "UNVERIFIED", f"blocked (HTTP {fr.http}) - verify in browser"
    elif fr.error is not None:
        status, signal = "UNVERIFIED", f"fetch error: {fr.error}"
    elif fr.http in (404, 410):
        status, signal = "CLOSED", f"HTTP {fr.http} - posting removed"
    elif fr.http != 200:
        status, signal = "UNVERIFIED", f"unexpected HTTP {fr.http} - verify in browser"
    else:
        status = classify_liveness(fr.text)
        if status == "OPEN" and source == "aggregator":
            # Guardrail: aggregators cache stale "apply" buttons long after the
            # employer closes. Their OPEN never overrides the canonical source —
            # downgrade to UNVERIFIED. (CLOSED from an aggregator is still trusted.)
            status = "UNVERIFIED"
            signal = (
                "aggregator OPEN is not authoritative (stale mirror) - "
                "confirm on the canonical LinkedIn/ATS posting in a browser"
            )
        else:
            signal = {
                "CLOSED": "matched a closed-posting signal",
                "OPEN": "matched an open/apply signal",
                "UNVERIFIED": "no clear open/closed signal - verify in browser",
            }[status]

    return LivenessResult(
        url=url,
        resolved_url=resolved,
        source=source,
        status=status,
        signal=signal,
        http=fr.http,
        checked_at=now,
    )


def main(argv: list[str] | None = None) -> int:
    force_utf8_output()
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print(__doc__)
        return 0
    worst = 0
    for url in args:
        r = check_url(url)
        print(r.line())
        if r.status == "CLOSED":
            worst = max(worst, 3)
        elif r.status == "UNVERIFIED":
            worst = max(worst, 2)
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
