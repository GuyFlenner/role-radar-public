"""Detects when the master CV has fallen behind the tailored-CV corpus.

Why this exists — `cv-aligner`'s pipeline is strictly one-way: master CV ->
`positions/{slug}/CV_TAILORED.md` per company. Nothing ever promotes a
wording/keyword improvement discovered while tailoring back to the master. See
`job-tracking/SYSTEM_RETRO_2026-08-11_master_cv_sync_gap.md` for the incident
that surfaced this: 50 tailoring commits vs 6 master-CV commits in six weeks,
with several genuinely better ATS terms (RAG, tool calling, evaluation-driven
development) sitting in tailored CVs and never reaching master.

This is a drift *detector*, not an auto-sync — content changes to the
candidate's master CV are a human judgment call, not something to apply
unattended. It flags candidate terms for review, in the same shape as
`src/jobcore/check.py` (compact report, exit code, --json), so it can be
wired into `job-flow-reviewer` or run standalone.

CLI:
    uv run python -m src.jobcore.cv_sync
    uv run python -m src.jobcore.cv_sync --min-occurrences 3 --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .console import force_utf8_output
from .gates import keyword_coverage

DEFAULT_MASTER_CV = "job-tracking/cv.md"
DEFAULT_POSITIONS_ROOT = "positions"
DEFAULT_WATCHLIST = "job-tracking/cv_sync_watchlist.txt"


def read_watchlist(path: Path) -> list[str]:
    """Read the committed term watchlist, ignoring blanks and # comments."""
    if not path.is_file():
        return []
    terms = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            terms.append(line)
    return terms


def find_tailored_cvs(positions_root: Path) -> list[Path]:
    """One level deep only (`positions/<slug>/CV_TAILORED.md`) — the same
    shape `check.py` and `cv-aligner` use. Folders whose slug starts with `_`
    (`_friends/`, `_template/`, `_legacy/`) are skipped explicitly, since a
    `CV_TAILORED.md` placed directly under one of those is not a real
    position and shouldn't count toward the drift threshold."""
    return sorted(p for p in positions_root.glob("*/CV_TAILORED.md") if not p.parent.name.startswith("_"))


def _present(text: str, term: str) -> bool:
    return keyword_coverage(text, [term])["pct"] == 100


def find_drift(
    master_text: str,
    tailored_texts: dict[str, str],
    watchlist: list[str],
    min_occurrences: int = 2,
) -> dict[str, list[str]]:
    """For each watchlist term missing from master, list the tailored-CV slugs
    that already carry it. A term only surfaces once at least
    `min_occurrences` tailored CVs converged on it — one company's one-off
    phrasing is not a master-CV candidate; several tailoring runs
    independently landing on the same term is a real signal.
    """
    drift: dict[str, list[str]] = {}
    for term in watchlist:
        if _present(master_text, term):
            continue
        hits = [slug for slug, text in tailored_texts.items() if _present(text, term)]
        if len(hits) >= min_occurrences:
            drift[term] = hits
    return drift


def main(argv: list[str] | None = None) -> int:
    force_utf8_output()
    ap = argparse.ArgumentParser(
        description="Flag terms that recur across tailored CVs but are missing from the master CV."
    )
    ap.add_argument("--master-cv", default=DEFAULT_MASTER_CV)
    ap.add_argument("--positions-root", default=DEFAULT_POSITIONS_ROOT)
    ap.add_argument("--watchlist", default=DEFAULT_WATCHLIST)
    ap.add_argument("--min-occurrences", type=int, default=2)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    master_path = Path(args.master_cv)
    if not master_path.is_file():
        print(f"Master CV not found: {master_path}", file=sys.stderr)
        return 2

    watchlist = read_watchlist(Path(args.watchlist))
    if not watchlist:
        print(f"No watchlist terms at {args.watchlist} — nothing to check.", file=sys.stderr)
        return 2

    tailored_paths = find_tailored_cvs(Path(args.positions_root))
    if not tailored_paths:
        print(f"No tailored CVs found under {args.positions_root} — nothing to check.", file=sys.stderr)
        return 2
    tailored_texts = {p.parent.name: p.read_text(encoding="utf-8") for p in tailored_paths}

    master_text = master_path.read_text(encoding="utf-8")
    drift = find_drift(master_text, tailored_texts, watchlist, args.min_occurrences)

    if args.json:
        print(json.dumps({"drift": drift, "checked_positions": len(tailored_texts)}, indent=2))
    else:
        print(f"Checked {len(tailored_texts)} tailored CVs against {len(watchlist)} watchlist terms.")
        if not drift:
            print("PASS  master CV has no drift vs. the tailored-CV corpus.")
        else:
            print(f"FAIL  {len(drift)} term(s) recur in tailored CVs but are missing from master:")
            for term, hits in drift.items():
                print(f"      -> '{term}' present in {len(hits)}: {', '.join(hits)}")

    return 0 if not drift else 1


if __name__ == "__main__":
    raise SystemExit(main())
