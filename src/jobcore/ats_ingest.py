"""ATS feed ingest — read target config, poll each company's board, score.

Mirrors csv_ingest's role for the ATS path: produce raw job dicts and hand them
to the shared `filter_score_positions` core. Also owns the delta "seen" store so
a self-refreshing poll can surface only what's new since the last run (like the
CSV path's .last_processed_csv), and tolerates per-company fetch failures.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .ats import REGISTRY, TEXT_PROVIDERS, linkedin
from .ats.base import http_get_json, http_get_text_status
from .csv_ingest import JOB_TRACKING, REPO_ROOT, JobKey, filter_score_positions, job_key

CONFIG_DEFAULT = REPO_ROOT / "ats_targets.json"
SEEN_STORE_DEFAULT = JOB_TRACKING / ".last_ats_seen.json"

# provider name → required config keys
_REQUIRED_KEYS = {
    "greenhouse": ("company", "token"),
    "comeet": ("company", "uid", "token"),
    "linkedin": ("company", "company_id"),
}


def load_targets(config_path: Path) -> list[dict]:
    """Read the JSON config → list of validated target dicts. Invalid entries skipped."""
    try:
        data = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"ats: cannot read config {config_path}: {str(e)[:120]}", file=sys.stderr)
        return []
    raw = data.get("targets", data) if isinstance(data, dict) else data
    if not isinstance(raw, list):
        print(f"ats: config {config_path} has no 'targets' list", file=sys.stderr)
        return []

    targets: list[dict] = []
    for t in raw:
        if not isinstance(t, dict):
            continue
        # From user-supplied JSON: could be absent, or not a string at all.
        ats = t.get("ats")
        required = _REQUIRED_KEYS.get(ats) if isinstance(ats, str) else None
        if not required:
            print(f"ats: skipping target with unknown ats={ats!r}: {t}", file=sys.stderr)
            continue
        missing = [k for k in required if not t.get(k)]
        if missing:
            print(f"ats: skipping {t.get('company', '?')} — missing {missing}", file=sys.stderr)
            continue
        targets.append(t)
    return targets


def _row_to_key(row) -> tuple | None:
    """Normalize one stored row into a canonical job_key tuple.

    Handles the current tagged shapes (``["id", <id>]`` / ``["tc", title, company]``)
    AND the legacy 2-element ``[title, company]`` store, so an existing
    ``.last_ats_seen.json`` keeps de-duping across the upgrade (no reset).
    """
    if not isinstance(row, list) or not row:
        return None
    tag = str(row[0]).lower()
    if tag == "id" and len(row) >= 2:
        return ("id", str(row[1]).lower())
    if tag == "tc" and len(row) >= 3:
        return ("tc", str(row[1]).lower(), str(row[2]).lower())
    if len(row) == 2:  # legacy [title, company]
        return ("tc", str(row[0]).lower(), str(row[1]).lower())
    return None


def load_seen(store: Path) -> set[tuple]:
    """Read the delta store → set of canonical job_key tuples seen last run."""
    try:
        rows = json.loads(Path(store).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(rows, list):
        return set()
    return {k for k in (_row_to_key(r) for r in rows) if k is not None}


def save_seen(store: Path, jobs: list[dict]) -> None:
    """Merge this run's canonical job_key tuples into the seen-store.

    **Accumulates** — it must not overwrite. Overwriting made the delta compare against only the
    immediately-previous run, so a role seen two runs ago re-surfaced as new and the "delta" list
    oscillated between "everything" and "almost nothing" on alternate runs.
    """
    merged = sorted({tuple(k) for k in load_seen(store)} | {job_key(j) for j in jobs})
    keys = [list(k) for k in merged]
    try:
        Path(store).parent.mkdir(parents=True, exist_ok=True)
        Path(store).write_text(json.dumps(keys, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        print(f"ats: could not write seen-store {store}: {str(e)[:120]}", file=sys.stderr)


def fetch_raw(targets: list[dict], get_json=http_get_json, get_text=http_get_text_status, providers=None) -> list[dict]:
    """Poll every target's board → flat list of raw job dicts. One dead board is skipped.

    Each provider gets the getter its payload needs: JSON-API providers
    (greenhouse/comeet) get ``get_json``; HTML-scraping providers (linkedin, see
    ``TEXT_PROVIDERS``) get ``get_text``, which defaults to the status-aware
    ``http_get_text_status`` so a rate-limit is retried and reported rather than
    silently read as an exhausted board. Per-target ``commute_extra`` locations
    are stamped onto every job so the geo gate can widen for that target only
    (e.g. Haifa for defense primes).
    """
    providers = providers if providers is not None else REGISTRY
    raw: list[dict] = []
    for t in targets:
        provider = providers.get(t["ats"])
        if provider is None:
            continue
        getter = get_text if t["ats"] in TEXT_PROVIDERS else get_json
        try:
            jobs = provider(t, getter)
        except Exception as e:  # noqa: BLE001 — never let one company abort the poll
            print(f"ats: {t.get('company', '?')} ({t['ats']}) failed: {str(e)[:120]}", file=sys.stderr)
            continue
        extra = t.get("commute_extra")
        if extra:
            for j in jobs:
                j["commute_extra"] = extra
        print(f"  ats: {t.get('company', '?')} ({t['ats']}) → {len(jobs)} roles", file=sys.stderr)
        raw.extend(jobs)
    return raw


def build_positions(
    targets: list[dict],
    prev_keys: set[JobKey] | None = None,
    delta_only: bool = True,
    apply_relevance: bool = True,
    require_preferred_location: bool = True,
    get_json=http_get_json,
    get_text=http_get_text_status,
    providers=None,
) -> tuple[list[dict], int]:
    """Fetch all targets and run them through the shared scoring core.

    ``require_preferred_location`` defaults True here (unlike the shared core's False):
    ATS boards are global, so the commute-band geo gate is on by default — pass
    False to see every geography.
    """
    raw = fetch_raw(targets, get_json=get_json, get_text=get_text, providers=providers)
    return filter_score_positions(
        raw,
        prev_keys=prev_keys,
        delta_only=delta_only,
        apply_relevance=apply_relevance,
        require_preferred_location=require_preferred_location,
    )


def build_market_positions(
    keywords,
    location: str = "",
    prev_keys: set[tuple] | None = None,
    delta_only: bool = True,
    apply_relevance: bool = True,
    require_preferred_location: bool = True,
    search=linkedin.search_market,
) -> tuple[list[dict], int]:
    """Company-agnostic LinkedIn market scan → scored positions.

    Runs a keyword+location LinkedIn guest search across ALL companies (no target
    config), then through the SAME deterministic core. This is the discovery tier
    the targeted poll can't provide — it catches great roles at companies not in
    ats_targets.json. ``search`` is injectable for offline tests.
    """
    raw = search(keywords, location=location)
    print(f"  ats: market search ({location}) → {len(raw)} raw roles", file=sys.stderr)
    return filter_score_positions(
        raw,
        prev_keys=prev_keys,
        delta_only=delta_only,
        apply_relevance=apply_relevance,
        require_preferred_location=require_preferred_location,
    )
