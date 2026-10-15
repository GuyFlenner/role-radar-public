"""Reviewer Pass B — liveness sweep over the candidate's *tracked* pursuits.

Pass A (in ats_shortlist) checks freshly-fetched listings. This pass closes the
other half: it re-checks the roles already under pursuit in ``positions/*/`` so a
req that CLOSED after you started chasing it gets caught — instead of finding out
when you show up. Deterministic, ~0 tokens: it reuses ``liveness.check_url`` and
the same "block ≠ closed" semantics (a LinkedIn 403 → UNVERIFIED, never CLOSED).

CLI::

    uv run python -m src.jobcore.track_sweep [--positions positions]
        [--out job-tracking/tracked_status.md]

Exit code: 0 = none closed, 3 = at least one tracked pursuit is CLOSED.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .console import force_utf8_output
from .csv_ingest import REPO_ROOT
from .liveness import check_url
from .pipeline import LIVENESS as LIVENESS_STATES

POSITIONS_DEFAULT = REPO_ROOT / "positions"
OUT_DEFAULT = REPO_ROOT / "job-tracking" / "tracked_status.md"

# Scaffolding / non-pursuit folders (and closed/occupied) — never swept.
# positions/_legacy/ holds roles that closed or were filled (e.g. Datadog — occupied).
_SKIP = frozenset({"_template", "_friends", "_archive", "_legacy", "seeking-alpha-return"})

# Files to read an apply URL from, in precedence order.
_JD_FILES = ("JD_DESCRIPTION.md", "JD_ANALYSIS.md")
_STATUS_FILES = ("STATUS.md",)

_URL_RE = re.compile(r"(https?://[^\s)]+)")
# Lines that carry the canonical apply/source link.
_APPLY_LINE = re.compile(r"apply\s*url|apply\s*via", re.I)
_SOURCE_LINE = re.compile(r"\bsource\b", re.I)
# The candidate's own profile — never an apply target.
_PROFILE_RE = re.compile(r"linkedin\.com/in/", re.I)

# Ordering for the report: closed first (act now), then unverified, then open, then no-url.
_STATUS_ORDER = {"CLOSED": 0, "UNVERIFIED": 1, "OPEN": 2, "NO_URL": 3}


def _clean(url: str) -> str:
    """Strip trailing punctuation a markdown line tends to append to a URL."""
    return url.rstrip(").,;|")


def _first_url_on_matching_line(text: str, line_pat: re.Pattern) -> str | None:
    for line in text.splitlines():
        if line_pat.search(line):
            m = _URL_RE.search(line)
            if m and not _PROFILE_RE.search(m.group(1)):
                return _clean(m.group(1))
    return None


def extract_apply_url(position_dir: Path) -> str | None:
    """Best-effort apply URL for a tracked position.

    Precedence: an ``Apply URL:``/``Apply via:`` line, then a ``Source:`` line, in
    the JD files, then STATUS files. The personal ``linkedin.com/in/`` profile is
    never returned. None if nothing usable is found (reported as NO_URL, not an error).
    """
    for group in (_JD_FILES, _STATUS_FILES):
        for name in group:
            f = position_dir / name
            if not f.exists():
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            for pat in (_APPLY_LINE, _SOURCE_LINE):
                url = _first_url_on_matching_line(text, pat)
                if url:
                    return url
    return None


def iter_positions(root: Path):
    """Yield pursuit directories under ``root``, skipping scaffolding/known-closed."""
    if not root.exists():
        return
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if d.name in _SKIP:
            continue
        yield d


def _ledger_index(ledger: dict | None) -> dict[str, dict]:
    """Map a positions/ folder name → its ledger row, for folders that have one."""
    if not ledger:
        return {}
    index: dict[str, dict] = {}
    for o in ledger.get("opportunities", []):
        folder = o.get("position_folder")
        if folder:
            index[Path(folder).name] = o
    return index


def _merge_liveness(previous: str | None, fresh: str, human: str | None = None) -> str:
    """Combine a stored ``posting_live`` with a fresh probe result.

    ``CLOSED`` is **sticky against an inconclusive re-check.** An earlier probe that positively
    read "this req is gone" is evidence; a later probe that could not reach a verdict is the
    absence of evidence, and letting absence overwrite evidence is how a dead posting climbs
    back into ACT NOW. WSC Sports did exactly that on 2026-08-05: found CLOSED in the morning
    sweep, downgraded to UNVERIFIED by an inconclusive afternoon re-check, and it reappeared as
    an actionable 8/10 role.

    Only a positive ``OPEN`` clears a ``CLOSED`` — that is a genuine repost, which does happen
    and must be caught. This is feedback_unverified_is_not_open read in the other direction:
    UNVERIFIED is not "open", so it must not be allowed to mean "not closed" either.

    ``human`` is a stored ``posting_checked`` verdict — someone opened the page and read it. That
    is evidence of the same class as a positive probe, so it outranks an inconclusive one but
    yields to a fresh ``OPEN``/``CLOSED``. Without this, the 63 rows whose apply page is
    JS-rendered would lose their hand-check on every sweep and ask to be checked forever.
    """
    if fresh in ("OPEN", "CLOSED"):
        return fresh
    if human in LIVENESS_STATES:
        return human
    return "CLOSED" if previous == "CLOSED" else "UNVERIFIED"


def sweep(root: Path, check=check_url, ledger: dict | None = None) -> list[dict]:
    """Re-check every tracked pursuit's apply URL. Returns rows sorted CLOSED-first.

    When a ``ledger`` is given, this becomes decision-aware:

    * pursuits whose ledger row is ``stage == "CLOSED"`` are **not probed** — you already
      decided about them, so a dead posting there is not news;
    * each result is written back to that row's ``posting_live`` and **nothing else**. A dead
      posting is a market fact; it never closes a pursuit. That separation is why an
      ``IN_PROCESS`` role with a ``CLOSED`` posting (the normal late-stage case) no longer
      reads as an emergency, and an archived one no longer reads as live.
    """
    index = _ledger_index(ledger)
    rows: list[dict] = []
    for d in iter_positions(root):
        row = index.get(d.name)
        if row is not None and row.get("stage") == "CLOSED":
            continue  # already decided — don't probe, don't report
        url = extract_apply_url(d)
        if not url:
            rows.append({"slug": d.name, "url": "", "status": "NO_URL", "signal": "no apply URL found"})
            continue
        r = check(url)
        if row is not None:
            checked = row.get("posting_checked") or {}
            row["posting_live"] = _merge_liveness(row.get("posting_live"), r.status, checked.get("status"))
        rows.append(
            {
                "slug": d.name,
                "url": url,
                "status": r.status,
                "signal": r.signal,
                "stage": (row or {}).get("stage", ""),
            }
        )
    rows.sort(key=lambda r: (_STATUS_ORDER.get(r["status"], 9), r["slug"]))
    return rows


def to_markdown(rows: list[dict]) -> str:
    closed = [r for r in rows if r["status"] == "CLOSED"]
    lines = [
        "# Tracked-position liveness sweep",
        "",
        f"{len(rows)} pursuits checked · **{len(closed)} CLOSED** · "
        f"{sum(1 for r in rows if r['status'] == 'UNVERIFIED')} UNVERIFIED · "
        f"{sum(1 for r in rows if r['status'] == 'OPEN')} OPEN",
        "",
        "| Status | Position | Signal | URL |",
        "|--------|----------|--------|-----|",
    ]
    for r in rows:
        flag = "🔴" if r["status"] == "CLOSED" else ("🟡" if r["status"] in ("UNVERIFIED", "NO_URL") else "🟢")
        lines.append(f"| {flag} {r['status']} | {r['slug']} | {r['signal']} | {r['url']} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    force_utf8_output()
    p = argparse.ArgumentParser(description="Liveness sweep over tracked positions/ pursuits.")
    p.add_argument("--positions", default=str(POSITIONS_DEFAULT), help="positions/ root")
    p.add_argument("--out", default=str(OUT_DEFAULT), help="Write the markdown report here")
    p.add_argument("--ledger", default=None, help="Pipeline ledger JSON (default: the repo ledger)")
    p.add_argument("--no-ledger", action="store_true", help="Sweep every folder blindly, ignoring the ledger")
    args = p.parse_args(argv)

    from . import pipeline

    ledger_path = Path(args.ledger) if args.ledger else pipeline.LEDGER_DEFAULT
    ledger = None if args.no_ledger else pipeline.load(ledger_path)

    # Pass `check_url` explicitly rather than relying on sweep's default: a default argument is
    # bound at definition time, which makes the liveness call un-injectable from main.
    rows = sweep(Path(args.positions), check=check_url, ledger=ledger)
    if ledger is not None:
        pipeline.save(ledger, ledger_path)

    if not rows:
        print("No tracked positions found.", file=sys.stderr)
        return 0

    md = to_markdown(rows)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(md, encoding="utf-8")

    closed = [r for r in rows if r["status"] == "CLOSED"]
    print(f"Tracked sweep: {len(rows)} checked, {len(closed)} posting-CLOSED → {args.out}", file=sys.stderr)
    for r in closed:
        print(f"  posting CLOSED: {r['slug']} — {r['url']}", file=sys.stderr)

    # Exit 3 = "something you are actively chasing just lost its posting" — worth a look.
    # A pursuit still at NEW/RESEARCHED losing its posting is routine market churn, and closed
    # pursuits were never swept, so neither should raise the alarm. Without a ledger there is no
    # stage to reason about, so fall back to the original behaviour: any CLOSED posting alarms.
    if ledger is None:
        return 3 if closed else 0
    alarming = [r for r in closed if r.get("stage") in pipeline.IN_FLIGHT_STAGES]
    return 3 if alarming else 0


if __name__ == "__main__":
    raise SystemExit(main())
