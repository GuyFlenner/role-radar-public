"""ats_shortlist CLI — the self-refreshing alternative to the Referally CSV.

Polls the public ATS boards listed in ats_targets.json, scores/dedups/ranks them
through the same deterministic core the CSV path uses, liveness-checks each
result's apply URL, and emits the SAME shortlist artifacts the job-seeking skill
already consumes (_shortlist.json + ranked markdown).

Usage:
  uv run python -m src.jobcore.ats_shortlist [--config ats_targets.json]
      [--out-json positions/_shortlist.json] [--out-md job-tracking/shortlist_ranked.md]
      [--from-scratch] [--raw] [--no-liveness]

Liveness (on by default): each shortlisted role's URL is checked; CLOSED roles
are dropped, UNVERIFIED are kept with a flag. --no-liveness skips it for speed.
This closes the stale-listing gap: a scraped feed is never surfaced for outreach
without confirming the posting is still open.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .ats_ingest import (
    CONFIG_DEFAULT,
    JOB_TRACKING,
    SEEN_STORE_DEFAULT,
    build_market_positions,
    build_positions,
    load_seen,
    load_targets,
    save_seen,
)
from .console import force_utf8_output
from .liveness import check_url
from .pipeline import API_LISTED_SOURCES

MARKET_SEEN_STORE = JOB_TRACKING / ".last_ats_market_seen.json"


def build_ats_shortlist(
    config_path: Path,
    from_scratch: bool = False,
    apply_relevance: bool = True,
    do_liveness: bool = True,
    require_preferred_location: bool = True,
    seen_store: Path | None = None,
    get_json=None,
    liveness_check=check_url,
    market_keywords=None,
    location: str = "",
    market_search=None,
) -> tuple[list[dict], int]:
    """Return (positions, stale_count). Positions are post-liveness (CLOSED dropped).

    ``require_preferred_location`` defaults True — global ATS boards are gated to the
    profile's preferred locations. The CLI exposes ``--anywhere`` to disable it.

    If ``market_keywords`` is given, run a **company-agnostic LinkedIn market scan**
    (keywords+location, no target config) instead of polling ats_targets.json — the
    discovery tier that catches roles at companies not on the target list.
    """
    if market_keywords:
        seen_store = seen_store or MARKET_SEEN_STORE
        prev_keys = set() if from_scratch else load_seen(seen_store)
        mkwargs = {"search": market_search} if market_search is not None else {}
        jobs, stale = build_market_positions(
            market_keywords,
            location=location,
            prev_keys=prev_keys,
            delta_only=not from_scratch,
            apply_relevance=apply_relevance,
            require_preferred_location=require_preferred_location,
            **mkwargs,
        )
    else:
        seen_store = seen_store or SEEN_STORE_DEFAULT
        targets = load_targets(config_path)
        if not targets:
            return [], 0
        prev_keys = set() if from_scratch else load_seen(seen_store)
        kwargs = {"get_json": get_json} if get_json is not None else {}
        jobs, stale = build_positions(
            targets,
            prev_keys=prev_keys,
            delta_only=not from_scratch,
            apply_relevance=apply_relevance,
            require_preferred_location=require_preferred_location,
            **kwargs,
        )

    # Record everything scored (pre-liveness) so a role that later closes isn't
    # re-surfaced next run, and delta reflects what we've actually seen.
    save_seen(seen_store, jobs)

    if not do_liveness:
        for j in jobs:
            j["liveness"] = "skipped"
        return jobs, stale

    live: list[dict] = []
    for j in jobs:
        url = j.get("url", "")
        if not url:
            j["liveness"] = _api_listed_or(j, "UNVERIFIED")
            live.append(j)
            continue
        status = liveness_check(url).status
        # A CLOSED verdict is always believed. An UNVERIFIED one is upgraded to LISTED when the
        # row came off a board's JSON API in THIS run: Comeet/Greenhouse apply pages render
        # status client-side, so the fetch can never succeed, yet the API returning the req is
        # authoritative evidence it is listed. Never the reverse — see LIVENESS in pipeline.py.
        j["liveness"] = _api_listed_or(j, status) if status == "UNVERIFIED" else status
        if status == "CLOSED":
            continue  # drop dead postings
        live.append(j)
    return live, stale


def _api_listed_or(job: dict, fallback: str) -> str:
    """``"LISTED"`` for a row sourced from an authoritative board API, else *fallback*."""
    provider = (job.get("source") or "").split(":", 1)[0].strip().lower()
    return "LISTED" if provider in API_LISTED_SOURCES else fallback


def _to_markdown(jobs: list[dict]) -> str:
    lines = [
        f"# ATS Shortlist — {len(jobs)} positions",
        "",
        "| # | Score | Live | Company | Role | Location | Preferred | Posted | Source | URL |",
        "|---|-------|------|---------|------|----------|-----------|--------|--------|-----|",
    ]
    for i, j in enumerate(jobs, 1):
        lines.append(
            f"| {i} | {j['match_score']}/10 | {j.get('liveness', '')} | {j['company']} | {j['title']} | "
            f"{j['location']} | {j.get('in_preferred_geo', '')} | {j.get('posted_str', '')} | "
            f"{j.get('source', '')} | {j.get('url', '')} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    force_utf8_output()
    p = argparse.ArgumentParser(description="Build a ranked shortlist from public ATS boards.")
    p.add_argument("--config", default=str(CONFIG_DEFAULT), help="Path to ats_targets.json")
    p.add_argument("--from-scratch", action="store_true", help="Ignore delta; process all roles")
    p.add_argument("--raw", action="store_true", help="Skip the relevance gate (full catalog)")
    p.add_argument("--no-liveness", action="store_true", help="Skip the per-URL liveness check")
    p.add_argument(
        "--anywhere",
        action="store_true",
        help="Disable the preferred-location geo filter (show every location)",
    )
    p.add_argument(
        "--market",
        help="Company-AGNOSTIC LinkedIn market scan: comma-separated keyword phrases "
        '(e.g. "AI full stack team lead,engineering manager AI"). Ignores ats_targets.json.',
    )
    p.add_argument("--location", default="", help="Location filter for --market (default: profile geo)")
    p.add_argument("--out-json", help="Write shortlist JSON here")
    p.add_argument("--out-md", help="Write ranked markdown here")
    args = p.parse_args(argv)

    market_keywords = [k.strip() for k in args.market.split(",") if k.strip()] if args.market else None

    config_path = Path(args.config)
    if not market_keywords and not config_path.exists():
        print(
            f"Config not found: {config_path}\n"
            f"Copy ats_targets.example.json → {config_path.name} and add your target boards.",
            file=sys.stderr,
        )
        return 2

    jobs, stale = build_ats_shortlist(
        config_path,
        from_scratch=args.from_scratch,
        apply_relevance=not args.raw,
        do_liveness=not args.no_liveness,
        require_preferred_location=not args.anywhere,
        market_keywords=market_keywords,
        location=args.location,
    )
    if not jobs:
        print("No positions after filtering (empty config, all stale/duplicate, or all closed).", file=sys.stderr)
        return 1

    if args.out_json:
        Path(args.out_json).write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Shortlist JSON: {args.out_json}")
    if args.out_md:
        Path(args.out_md).write_text(_to_markdown(jobs), encoding="utf-8")
        print(f"Shortlist MD: {args.out_md}")
    if not args.out_json and not args.out_md:
        print(_to_markdown(jobs))

    print(f"\n{len(jobs)} positions shortlisted (top score {jobs[0]['match_score']}/10).", file=sys.stderr)

    # Keep the durable ledger current so the board reflects this poll without a second command.
    # Additive and non-authoritative: upsert never touches stage/outcome/history, and a ledger
    # failure must never fail the poll itself (the shortlist artifacts are already written).
    if not market_keywords:
        try:
            from . import pipeline

            ledger = pipeline.load(pipeline.LEDGER_DEFAULT)
            ins, upd = pipeline.upsert(ledger, jobs)
            pipeline.save(ledger, pipeline.LEDGER_DEFAULT)
            Path(pipeline.BOARD_DEFAULT).write_text(pipeline.render(ledger), encoding="utf-8")
            print(f"pipeline: +{ins} new / {upd} updated → {pipeline.BOARD_DEFAULT}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 — ledger is additive; never fail the poll on it
            print(f"pipeline: ledger update skipped ({e})", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
