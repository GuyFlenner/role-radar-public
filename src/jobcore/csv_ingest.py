"""Job-board CSV ingest — parse, format-detect, delta, dedup, stale-drop, enrich.

Handles both real-world export shapes (legacy semicolon + WhatsApp comma).
Deterministic, no LLM. Scoring/relevance come from .scoring.
"""

import csv
import re
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from .profile import Profile
from .scoring import is_relevant, level_band, location_allowed, score

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
JOB_TRACKING = REPO_ROOT / "job-tracking"
#: Fallback glob when the profile sets no ``csv.pattern``. Deliberately broad: a pattern that
#: matches nothing produces an empty shortlist that reads as "quiet market" rather than
#: "misconfigured", which is the failure mode this whole module used to have.
DEFAULT_CSV_PATTERN = "*.csv"
LAST_PROCESSED_FILE = JOB_TRACKING / ".last_processed_csv"

#: A canonical dedup/delta key: ``("id", <posting id>)`` OR ``("tc", <title>, <company>)``.
#: Declared as variadic because it is genuinely both — every signature here previously said
#: `tuple[str, str]` while `job_key` had been returning 3-tuples for the `tc` case since the
#: tagging change. Nothing crashed (Python does not enforce it) but the annotation was false,
#: which is the kind of small lie that makes the next reader distrust every other annotation.
JobKey = tuple[str, ...]

STALE_AFTER_DAYS = 60  # positions older than this are skipped without any API calls (~2 months)


def csv_pattern() -> str:
    """The glob identifying the job-board export — from the profile, not hardcoded.

    Was ``"Referally Leadership*.csv"``: one job board's export filename. A second user pointing
    this at their own CSV matched zero files and got no error.
    """
    from .profile import load

    return str((load().raw.get("csv") or {}).get("pattern") or "") or DEFAULT_CSV_PATTERN


def sender_filter() -> str:
    """Only rows whose sender column contains this substring are ingested ("" = keep all).

    Was the hardcoded literal ``"Referally"`` tested inline at two call sites.
    """
    from .profile import load

    return str((load().raw.get("csv") or {}).get("sender_filter") or "")


#: Back-compat alias for callers that imported the constant. Prefer :func:`csv_pattern`.
CSV_PATTERN = DEFAULT_CSV_PATTERN


def find_latest_csv(pattern: str | None = None) -> Path | None:
    csv_files = list(REPO_ROOT.glob(pattern if pattern is not None else csv_pattern()))
    if not csv_files:
        return None
    csv_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return csv_files[0]


# --- CSV format detection ---------------------------------------------------
# 1. Legacy structured export: semicolon, columns MessageBody/UserName/Date1/Date2 (ISO date).
# 2. WhatsApp group export: comma, columns Message/Display Name/Time (US M/D/YYYY),
#    utf-8 BOM, bidirectional control marks in the body.


def _detect_format(fieldnames: Sequence[str] | None) -> dict:
    names = set(fieldnames or [])
    if "Message" in names and "Display Name" in names:
        return {
            "delimiter": ",",
            "body_col": "Message",
            "sender_col": "Display Name",
            "date_cols": ["Time"],
            "date_fmt": "%m/%d/%Y",
        }
    return {
        "delimiter": ";",
        "body_col": "MessageBody",
        "sender_col": "UserName",
        "date_cols": ["Date2", "Date1"],
        "date_fmt": "%Y-%m-%d",
    }


def _open_rows(csv_path: Path):
    """Return (rows, spec). Sniff the delimiter from the header line first."""
    with open(csv_path, newline="", encoding="utf-8-sig", errors="replace") as f:
        first_line = f.readline()
        delimiter = "," if first_line.count(",") >= first_line.count(";") else ";"
        f.seek(0)
        reader = csv.DictReader(f, delimiter=delimiter)
        spec = _detect_format(reader.fieldnames)
        rows = list(reader)
    return rows, spec


def _strip_marks(text: str) -> str:
    """Remove bidirectional control marks that WhatsApp injects before * and _."""
    return text.replace("‎", "").replace("‏", "")


def _parse_date(raw: str, fmt: str):
    raw = (raw or "").strip().strip('"')
    if not raw:
        return None
    raw = raw.split(",")[0].strip()  # WhatsApp Time: keep date part before the comma
    try:
        return datetime.strptime(raw, fmt).date()
    except ValueError:
        if re.match(r"\d{4}-\d{2}-\d{2}", raw):
            try:
                return datetime.strptime(raw[:10], "%Y-%m-%d").date()
            except ValueError:
                return None
        return None


def get_last_processed_set() -> set[JobKey]:
    if not LAST_PROCESSED_FILE.exists():
        return set()
    last_name = LAST_PROCESSED_FILE.read_text().strip()
    last_path = REPO_ROOT / last_name
    if not last_path.exists():
        return set()
    return _load_csv_job_keys(last_path)


def _sender_ok(row: dict, spec: dict, wanted: str | None = None) -> bool:
    """True if this row's sender passes the profile's ``csv.sender_filter``.

    An empty filter keeps every row. Previously the literal ``"Referally"`` was compared inline
    at both call sites, so any other user's export was filtered down to nothing — silently.
    """
    want = sender_filter() if wanted is None else wanted
    return not want or want.lower() in str(row.get(spec["sender_col"], "")).lower()


def _load_csv_job_keys(csv_path: Path) -> set[JobKey]:
    keys: set[JobKey] = set()
    try:
        rows, spec = _open_rows(csv_path)
        for row in rows:
            body = _strip_marks((row.get(spec["body_col"]) or "").strip())
            if not body or not _sender_ok(row, spec):
                continue
            m = re.search(r"\*([^*]+)\*\s*/\s*([^\n\\]+)", body)
            if m:
                # CSV rows carry no stable id → the ("tc", title, company) fallback,
                # matching job_key() so the delta compares apples to apples.
                keys.add(("tc", m.group(1).strip().lower(), m.group(2).strip().lower()))
    except Exception as e:
        print(f"Warning reading {csv_path}: {e}", file=sys.stderr)
    return keys


def _extract_job(row: dict, spec: dict) -> dict | None:
    body = _strip_marks((row.get(spec["body_col"]) or "").strip())
    if not body or not _sender_ok(row, spec):
        return None
    raw_date = ""
    for col in spec["date_cols"]:
        if row.get(col):
            raw_date = row.get(col) or ""
            break
    posted_date = _parse_date(raw_date, spec["date_fmt"])
    m = re.search(r"\*([^*]+)\*\s*/\s*([^\n\\]+)", body)
    title = m.group(1).strip() if m else ""
    company = m.group(2).strip() if m else ""
    url_m = re.search(r"(https?://[^\s\n]+)", body)
    url = url_m.group(1).strip() if url_m else ""
    loc_m = re.search(r"_\s*([^_]+)\s*_\s*\|\s*_\s*([^_]+)\s*_", body)
    location = loc_m.group(1).strip() if loc_m else ""
    domain = loc_m.group(2).strip() if loc_m else ""
    return {
        "title": title,
        "company": company,
        "location": location,
        "domain": domain,
        "url": url,
        "snippet": body[:500],
        "posted_date": posted_date,
        "posted_str": posted_date.strftime("%Y-%m-%d") if posted_date else "",
    }


def _get_recency(posted_date) -> tuple[str, int]:
    if not posted_date:
        return ("Unknown", 1)
    today = datetime.now().date()
    if isinstance(posted_date, datetime):
        posted_date = posted_date.date()
    days = (today - posted_date).days
    if days <= 31:
        return ("Recent (<=1 mo)", 3)
    if days <= 60:
        return ("1-2 months", 2)
    if days <= 90:
        return ("2-3 months", 1)
    return ("Old (4+ mo)", 0)


def slugify(text: str) -> str:
    """Convert a company or role name to a folder-safe kebab-case slug.

    Runs of hyphens collapse to one, and edge hyphens are stripped. Without that, a literal
    hyphen survives the punctuation strip while the spaces *around* it also become hyphens — so
    "Full Stack Team Lead - AI team" slugged to ``full-stack-team-lead---ai-team`` while the
    comma-spelled "Full Stack Team Lead, AI Team" slugged to ``full-stack-team-lead-ai-team``.
    ``pipeline.make_id`` is built from this, so one requisition could exist as two ledger rows.
    """
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[\s_]+", "-", slug.strip())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug[:40].strip("-")


def job_key(job: dict) -> JobKey:
    """Canonical dedup/delta key for a raw job — the SINGLE source of truth.

    Prefers the provider's stable posting id (``job_id``: Greenhouse job id,
    Comeet uid, LinkedIn job-id) so a re-titled req isn't re-surfaced and a
    re-opened one isn't missed. Falls back to a ``("tc", title, company)`` tuple
    when no id is available (the CSV path, which has none). The tag
    keeps the two key spaces from ever colliding.
    """
    jid = (job.get("job_id") or "").strip().lower()
    if jid:
        return ("id", jid)
    return ("tc", (job.get("title") or "").lower(), (job.get("company") or "").lower())


def filter_score_positions(
    raw_jobs,
    prev_keys: set[JobKey] | None = None,
    delta_only: bool = True,
    stale_after_days: int = STALE_AFTER_DAYS,
    apply_relevance: bool = True,
    require_preferred_location: bool = False,
    profile: Profile | None = None,
) -> tuple[list[dict], int]:
    """Shared deterministic tail: relevance → dedup → delta → stale → geo → score → sort.

    Provider-agnostic. Takes an iterable of raw job dicts (the shape ``_extract_job``
    produces: title/company/location/domain/url/snippet/posted_date[/source]) and
    returns ``(scored_positions, stale_skipped_count)``. Both the CSV path
    (``load_new_positions``) and the ATS-feed path (``ats_ingest``) call this — one
    source of truth for scoring/filtering so the two feeds can never drift.

    - ``apply_relevance=False`` skips the title/domain gate (raw catalog).
    - ``delta_only=True`` skips positions whose (title, company) key is in ``prev_keys``.
    - positions with a known date older than ``stale_after_days`` are dropped.
    - ``require_preferred_location=True`` drops positions outside the profile's
      ``geo.preferred_locations`` (plus any per-target ``commute_extra``). Off by default.
      Note the profile's own ``geo.mode`` still wins: under ``"preferred"`` nothing is
      dropped and location becomes a ranking flag, which is what lets this run outside a
      single commute band.
    """
    prev_keys = prev_keys or set()

    jobs: list[dict] = []
    seen: set[JobKey] = set()
    stale_count = 0
    today = datetime.now().date()

    for job in raw_jobs:
        if not job or not job.get("title"):
            continue
        if apply_relevance and not is_relevant(job, profile):
            continue
        key = job_key(job)
        if key in seen:
            continue
        seen.add(key)
        if delta_only and key in prev_keys:
            continue

        posted = job.get("posted_date")
        if posted:
            if isinstance(posted, datetime):
                posted = posted.date()
            if (today - posted).days > stale_after_days:
                stale_count += 1
                continue

        # Geo gate: the profile's preferred locations, plus any per-target `commute_extra`
        # (e.g. one employer outside the band — never widened globally).
        is_allowed, is_center = location_allowed(job, profile)
        if require_preferred_location and not is_allowed:
            continue

        recency_label, _ = _get_recency(posted)
        jobs.append(
            {
                "title": job["title"],
                "company": job["company"],
                "company_slug": slugify(job["company"]),
                "location": job.get("location", ""),
                "domain": job.get("domain", ""),
                "url": job.get("url", ""),
                # Carried through so `save_seen` derives the SAME key `job_key` used for the
                # delta comparison above. Dropping it silently downgraded every ATS row's key
                # to ("tc", title, company) while the comparison used ("id", job_id) — the
                # delta could never fire. See test_delta_suppresses_ats_job_on_second_run.
                "job_id": job.get("job_id", ""),
                "snippet": job.get("snippet", ""),
                "match_score": score(job, profile),
                # Altitude, independent of match_score — above-band roles are ranked below
                # in-band ones on the board rather than dropped.
                "level_band": level_band(job, profile),
                "recency_label": recency_label,
                "posted_str": job.get("posted_str", ""),
                "in_preferred_geo": "Yes" if is_center else "",
                "source": job.get("source", "referally"),
            }
        )

    jobs.sort(key=lambda j: -j["match_score"])
    return jobs, stale_count


def load_new_positions(
    csv_path: Path,
    delta_only: bool = True,
    stale_after_days: int = STALE_AFTER_DAYS,
    apply_relevance: bool = True,
) -> tuple[list[dict], int]:
    """
    Parse the job-board CSV → (positions, stale_skipped_count).
    Positions with a known date older than `stale_after_days` are dropped.
    delta_only=True also skips positions seen in the previous run.
    apply_relevance=False skips the title/domain gate (raw catalog).
    """
    prev_keys = get_last_processed_set() if delta_only else set()
    rows, spec = _open_rows(csv_path)
    raw_jobs = (_extract_job(row, spec) for row in rows)
    return filter_score_positions(
        raw_jobs,
        prev_keys=prev_keys,
        delta_only=delta_only,
        stale_after_days=stale_after_days,
        apply_relevance=apply_relevance,
    )


def mark_csv_processed(csv_path: Path) -> None:
    LAST_PROCESSED_FILE.write_text(csv_path.name)
