"""shortlist CLI — the deterministic pre-processing step of the batch pipeline.

Turns a Referally CSV into a ranked, deduped, stale-dropped, scored shortlist
(JSON + markdown). The native job-seeking skill calls this, then fans out one
subagent per shortlisted company. No LLM here — determinism where it matters.

Usage:
  uv run python -m src.jobcore.shortlist --csv "Referally Leadership X.csv"
      [--from-scratch] [--raw] [--out-json PATH] [--out-md PATH]
"""

import argparse
import json
import sys
from pathlib import Path

from .console import force_utf8_output
from .csv_ingest import load_new_positions


def build_shortlist(csv_path: Path, from_scratch: bool = False, apply_relevance: bool = True) -> list[dict]:
    """Return positions sorted by match_score desc (deduped, stale-dropped, scored)."""
    jobs, _stale = load_new_positions(
        csv_path,
        delta_only=not from_scratch,
        apply_relevance=apply_relevance,
    )
    return jobs


def _to_markdown(jobs: list[dict]) -> str:
    lines = [
        f"# Ranked Shortlist — {len(jobs)} positions",
        "",
        "| # | Score | Company | Role | Location | Preferred | Posted | URL |",
        "|---|-------|---------|------|----------|-----------|--------|-----|",
    ]
    for i, j in enumerate(jobs, 1):
        lines.append(
            f"| {i} | {j['match_score']}/10 | {j['company']} | {j['title']} | "
            f"{j['location']} | {j.get('in_preferred_geo', '')} | {j.get('posted_str', '')} | {j.get('url', '')} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    force_utf8_output()
    p = argparse.ArgumentParser(description="Build a ranked job shortlist from a Referally CSV.")
    p.add_argument("--csv", required=True, help="Path to the Referally CSV")
    p.add_argument("--from-scratch", action="store_true", help="Ignore delta; process all rows")
    p.add_argument("--raw", action="store_true", help="Skip the relevance gate (full catalog)")
    p.add_argument("--out-json", help="Write shortlist JSON here")
    p.add_argument("--out-md", help="Write ranked markdown here")
    args = p.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"CSV not found: {csv_path}", file=sys.stderr)
        return 2

    jobs = build_shortlist(csv_path, from_scratch=args.from_scratch, apply_relevance=not args.raw)
    if not jobs:
        print("No positions after filtering (empty CSV or all stale/duplicate).", file=sys.stderr)
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
