"""One-shot content-gate runner for position packages.

Why this exists — a token-cost fix, not just ergonomics. Every tool call in an
agentic session re-sends the whole conversation prefix, billed as cache-read.
Measured on a real session: ~271k tokens of context per request, i.e. ~$0.14 of
cache-read per round trip at Opus input rates. The `cv-aligner` flow ran the
gate battery as **eight separate** invocations per CV (coverage, facts, drift,
pitch, knockouts, over-qualification, page count, cover-letter drift), so a
three-CV batch spent ~24 round trips on checks that need only one.

This collapses the battery into a single call over any number of positions and
prints one compact line each, so it costs one round trip and adds little to the
prefix that every later request re-reads.

Second benefit: the ATS keyword list stops being prose retyped into an ad-hoc
heredoc on every run (itself a drift risk) and becomes a committed artifact at
``positions/<slug>/gate_terms.txt``.

Exit status: 0 = every position passed, 1 = at least one failed, 2 = bad usage.

CLI:
    uv run python -m src.jobcore.check positions/cyabra positions/varonis
    uv run python -m src.jobcore.check positions/* --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .console import force_utf8_output
from .gates import (
    check_cv_facts,
    find_company_pitch_in_cv,
    find_cover_letter_drift,
    find_knockout_gaps,
    find_private_scope_leak,
    find_repeated_clauses,
    keyword_coverage,
    overqualification_note,
)

DEFAULT_MASTER_CV = "job-tracking/cv.md"
MAX_PAGES = 2
TERMS_FILENAME = "gate_terms.txt"


@dataclass
class PositionResult:
    """Outcome of the whole gate battery for one position folder."""

    slug: str
    company: str | None = None
    coverage_pct: int | None = None
    missing_terms: list[str] = field(default_factory=list)
    fact_violations: list[str] = field(default_factory=list)
    repeated_clauses: list[str] = field(default_factory=list)
    pitch_hits: list[str] = field(default_factory=list)
    knockouts: list[str] = field(default_factory=list)
    cover_letter_copies: list[str] = field(default_factory=list)
    private_scope_hits: list[str] = field(default_factory=list)
    pages: int | None = None
    overqualified: str | None = None  # advisory only — never fails
    errors: list[str] = field(default_factory=list)

    @property
    def failures(self) -> list[str]:
        """Blocking problems only. Over-qualification is deliberately excluded."""
        out: list[str] = []
        if self.errors:
            out += [f"error: {e}" for e in self.errors]
        if self.coverage_pct is not None and self.coverage_pct < 100:
            out.append(f"coverage {self.coverage_pct}% (missing: {', '.join(self.missing_terms)})")
        if self.fact_violations:
            out.append(f"facts: {'; '.join(self.fact_violations)}")
        if self.repeated_clauses:
            out.append(f"drift: {len(self.repeated_clauses)} repeated clause(s)")
        if self.pitch_hits:
            out.append(f"pitch: {'; '.join(self.pitch_hits)}")
        if self.knockouts:
            out.append(f"knockouts: {'; '.join(self.knockouts)}")
        if self.cover_letter_copies:
            out.append(f"cover copies CV: {len(self.cover_letter_copies)} clause(s)")
        if self.private_scope_hits:
            out.append(f"private scope: {'; '.join(self.private_scope_hits)}")
        if self.pages is not None and self.pages > MAX_PAGES:
            out.append(f"PDF {self.pages} pages (max {MAX_PAGES})")
        return out

    @property
    def passed(self) -> bool:
        return not self.failures


def company_from_jd(jd_text: str) -> str | None:
    """Pull the company name out of a JD's first H1, e.g. '# Cyabra — Backend TL'."""
    for line in jd_text.splitlines():
        if line.startswith("# "):
            heading = line[2:].strip()
            # Split on an em/en dash or a hyphen surrounded by spaces.
            parts = re.split(r"\s+[—–]\s+|\s+-\s+", heading, maxsplit=1)
            name = parts[0].strip()
            return name or None
    return None


def read_terms(folder: Path) -> list[str]:
    """Read the committed ATS term list, ignoring blanks and # comments."""
    path = folder / TERMS_FILENAME
    if not path.is_file():
        return []
    terms = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            terms.append(line)
    return terms


def _find_pdf(folder: Path) -> Path | None:
    pdfs = sorted(folder.glob("CV_*.pdf"))
    return pdfs[0] if pdfs else None


def check_position(folder: Path, master_cv: str = DEFAULT_MASTER_CV) -> PositionResult:
    """Run every content gate for one position folder."""
    res = PositionResult(slug=folder.name)

    cv_path = folder / "CV_TAILORED.md"
    if not cv_path.is_file():
        res.errors.append("no CV_TAILORED.md")
        return res
    cv = cv_path.read_text(encoding="utf-8")

    jd_path = folder / "JD_DESCRIPTION.md"
    jd = jd_path.read_text(encoding="utf-8") if jd_path.is_file() else ""
    res.company = company_from_jd(jd) if jd else None

    terms = read_terms(folder)
    if terms:
        cov = keyword_coverage(cv, terms)
        res.coverage_pct = cov["pct"]
        res.missing_terms = list(cov["missing"])

    res.fact_violations = list(check_cv_facts(cv))
    res.repeated_clauses = [c for c, _ in find_repeated_clauses(cv)]
    res.pitch_hits = list(find_company_pitch_in_cv(cv, res.company))
    res.private_scope_hits = list(find_private_scope_leak(cv))

    master_path = Path(master_cv)
    if jd and master_path.is_file():
        master = master_path.read_text(encoding="utf-8")
        res.knockouts = list(find_knockout_gaps(jd, master))
        res.overqualified = overqualification_note(jd, master)

    cover = folder / "COVER_LETTER.md"
    if cover.is_file():
        cl = cover.read_text(encoding="utf-8")
        res.cover_letter_copies = [c for c, _ in find_cover_letter_drift(cl, cv)]
        res.private_scope_hits += list(find_private_scope_leak(cl))

    pdf = _find_pdf(folder)
    if pdf is not None:
        try:
            from .pdf import page_count

            res.pages = page_count(pdf)
        except Exception as exc:  # pragma: no cover - depends on optional deps
            res.errors.append(f"page_count failed: {exc}")

    return res


def format_line(res: PositionResult) -> str:
    """One compact line per position — the whole point of this module."""
    status = "PASS" if res.passed else "FAIL"
    cov = "n/a" if res.coverage_pct is None else f"{res.coverage_pct}%"
    pages = "n/a" if res.pages is None else f"{res.pages}p"
    line = (
        f"{status}  {res.slug:<18} cov={cov:<5} facts={len(res.fact_violations)} "
        f"drift={len(res.repeated_clauses)} pitch={len(res.pitch_hits)} "
        f"knock={len(res.knockouts)} private={len(res.private_scope_hits)} pdf={pages}"
    )
    if not res.passed:
        line += "\n      → " + "\n      → ".join(res.failures)
    return line


def main(argv: list[str] | None = None) -> int:
    force_utf8_output()
    ap = argparse.ArgumentParser(
        description="Run every CV content gate for one or more position folders in a single call."
    )
    ap.add_argument("folders", nargs="+", help="position folders, e.g. positions/cyabra")
    ap.add_argument("--master-cv", default=DEFAULT_MASTER_CV, help="path to the master CV")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of the compact report")
    args = ap.parse_args(argv)

    folders = [Path(f) for f in args.folders if Path(f).is_dir()]
    if not folders:
        print("No valid position folders given.", file=sys.stderr)
        return 2

    results = [check_position(f, args.master_cv) for f in folders]

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "slug": r.slug,
                        "company": r.company,
                        "passed": r.passed,
                        "coverage_pct": r.coverage_pct,
                        "failures": r.failures,
                        "overqualification_advisory": r.overqualified,
                        "pages": r.pages,
                    }
                    for r in results
                ],
                indent=2,
            )
        )
    else:
        for r in results:
            print(format_line(r))
        advisories = [r for r in results if r.overqualified]
        if advisories:
            print(f"\nadvisory (never blocking): over-qualification on {len(advisories)} position(s)")
        failed = [r for r in results if not r.passed]
        print(
            f"\n{len(results) - len(failed)}/{len(results)} passed"
            + ("" if not failed else f" — FAILED: {', '.join(r.slug for r in failed)}")
        )

    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
