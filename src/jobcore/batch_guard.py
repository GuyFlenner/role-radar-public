"""Deterministic guard for hands-off (``AUTO_APPROVE``) runs — the enforcement half of a rule
that until now existed only in prose.

**Why this exists.** "Never run a `job-seeking` batch with ``AUTO_APPROVE=1``" was written in 14
files across this repo — CLAUDE.md, README, the Pro-plan operating guide, the migration specs, the
skill itself. An audit on 2026-08-05 found **31 occurrences and not one line of executable code**
that read the variable. A rule enforced only by prose is the same defect shape as a regex that can
never fire: the passing state and the broken state are indistinguishable, and the only thing
standing between a user and 30 unattended parallel subagents is whether someone remembered.
See ``feedback_a_green_control_that_means_nothing`` in project memory.

**Two separate questions, one module**, because
``docs/superpowers/specs/2026-08-05-job-apply-orchestrator-design.md`` explicitly warns against
duplicating gate logic across skills:

1. :func:`check_batch` — *may this hands-off batch run at this size?* A cap on **distinct
   companies**, since fan-out is per company. Only applies when unattended: with a human clearing
   gates, 30 companies is slow, not dangerous, so this must never become a general batch limit.
2. :func:`gate_requires_human` — *can ``AUTO_APPROVE`` skip THIS gate?* :data:`NEVER_SKIPPABLE`
   is the registry of irreversible steps (anything that puts a CV or a message in front of a real
   person). ``job-apply``'s Phase-3 gate and ``job-seeking``'s submit gate ask the same function,
   so the answer cannot drift between them.

Deterministic: zero model calls, stdlib only.

CLI::

    uv run python -m src.jobcore.batch_guard --shortlist positions/_shortlist.json

Exit codes: ``0`` allowed · ``1`` **refused** (with the reason on stdout) · ``2`` bad usage.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .console import force_utf8_output

#: Env var the skills document as the hands-off switch.
AUTO_APPROVE_ENV = "AUTO_APPROVE"

#: Env var that raises the cap for a deliberate, larger unattended run.
MAX_COMPANIES_ENV = "MAX_HANDS_OFF_COMPANIES"

#: Companies per unattended batch. 5 because that is the figure the Pro-plan operating guide
#: already commits to ("cap batches at 3–5 companies") — this module enforces the number the docs
#: were already promising, rather than inventing a new one.
DEFAULT_MAX_HANDS_OFF_COMPANIES = 5

#: Values that mean "yes" for a shell-style boolean env var.
_TRUTHY = frozenset({"1", "true", "yes", "y", "on"})

#: Gates that ``AUTO_APPROVE`` must NEVER skip, however the run was started. These are the
#: irreversible ones: a CV that reached a recruiter cannot be unsent, and a duplicate application
#: through a competing route can cost an agency-fee dispute. Everything else is a convenience
#: pause and is legitimately skippable.
NEVER_SKIPPABLE = frozenset(
    {
        "submit",  # the CV actually goes out
        "outreach",  # a message to a named human
        "apply",  # job-apply's Phase 3 "ready to submit" gate
        "route",  # resolving which of two competing routes to use
        "lead_approval",  # email-lead-triage's score+explanation yes/no before job-apply
    }
)

#: Gates that a hands-off run may clear on its own — a convenience pause, not a decision.
SKIPPABLE = frozenset({"shortlist", "research", "cv", "prep", "summary"})


def auto_approve_enabled(env: dict[str, str] | None = None) -> bool:
    """True when the hands-off switch is set. Anything not explicitly truthy is off."""
    raw = (env or os.environ).get(AUTO_APPROVE_ENV, "")
    return raw.strip().lower() in _TRUTHY


def hands_off_cap(env: dict[str, str] | None = None) -> int:
    """The company cap, from :data:`MAX_COMPANIES_ENV` when it is a sane positive integer.

    A nonsense value falls back to the default rather than disabling the guard — a control that
    silently switches itself off on bad config is worse than no control at all.
    """
    raw = (env or os.environ).get(MAX_COMPANIES_ENV, "")
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_MAX_HANDS_OFF_COMPANIES
    return value if value > 0 else DEFAULT_MAX_HANDS_OFF_COMPANIES


def companies_in(shortlist) -> list[str]:
    """Distinct company names from a shortlist, sorted, case- and whitespace-insensitive.

    Counts **companies, not rows**: fan-out is one subagent per company, and two reqs at one
    company is one target. Cyera had two 8/10 reqs on the 2026-08-05 board, so this is the normal
    case. Accepts either a bare list (what the shortlist CLIs emit today) or an object wrapping one
    under ``positions``/``rows``, so a future wrapper cannot make the guard count zero and wave
    everything through.
    """
    rows = shortlist
    if isinstance(shortlist, dict):
        rows = shortlist.get("positions") or shortlist.get("rows") or []
    seen: dict[str, str] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("company") or "").strip()
        if name:
            seen.setdefault(name.lower(), name)
    return sorted(seen.values(), key=str.lower)


@dataclass(frozen=True)
class Verdict:
    """The guard's answer. ``exit_code`` mirrors :func:`main` so callers agree by construction."""

    allowed: bool
    company_count: int
    cap: int
    auto_approve: bool
    reason: str

    @property
    def exit_code(self) -> int:
        return 0 if self.allowed else 1


def check_batch(shortlist, auto_approve: bool | None = None, cap: int | None = None) -> Verdict:
    """Decide whether this batch may run. See the module docstring for the reasoning."""
    auto_approve = auto_approve_enabled() if auto_approve is None else auto_approve
    cap = hands_off_cap() if cap is None else cap
    companies = companies_in(shortlist)
    count = len(companies)

    # ASCII only in these strings: the skill is told to print the reason VERBATIM, and a cp1252
    # Windows console turns an em-dash into a replacement char mid-sentence.
    if not auto_approve:
        return Verdict(True, count, cap, False, f"attended run ({count} companies) - human clears each gate")
    if count <= cap:
        return Verdict(True, count, cap, True, f"hands-off run of {count} companies is within the cap of {cap}")
    return Verdict(
        False,
        count,
        cap,
        True,
        f"hands-off batch of {count} companies exceeds the cap of {cap}. "
        f"Unattended fan-out spawns one subagent per company with no gate to stop it. "
        f"Either drop AUTO_APPROVE and clear the gates yourself, narrow the shortlist to "
        f"{cap} companies or fewer, or set {MAX_COMPANIES_ENV} deliberately if you mean it.",
    )


def gate_requires_human(gate: str, auto_approve: bool | None = None) -> bool:
    """True when *gate* must pause for a person.

    ``AUTO_APPROVE`` clears a gate only if it is explicitly listed in :data:`SKIPPABLE`. An
    unrecognised name **fails closed** — a typo, or a gate added later without touching this
    registry, must not silently become skippable. That is the property
    ``2026-08-05-job-apply-orchestrator-design.md`` needs for its Phase-3 gate.
    """
    auto_approve = auto_approve_enabled() if auto_approve is None else auto_approve
    if not auto_approve:
        return True
    return (gate or "").strip().lower() not in SKIPPABLE


def main(argv: list[str] | None = None) -> int:
    force_utf8_output()
    p = argparse.ArgumentParser(description="Refuse an oversized hands-off (AUTO_APPROVE) batch.")
    p.add_argument("--shortlist", required=True, help="Path to the shortlist JSON to be fanned out")
    p.add_argument(
        "--auto-approve",
        action="store_true",
        help=f"Treat this as a hands-off run regardless of ${AUTO_APPROVE_ENV} (for testing/CI)",
    )
    p.add_argument("--cap", type=int, help=f"Override the company cap (default ${MAX_COMPANIES_ENV} or 5)")
    args = p.parse_args(argv)

    path = Path(args.shortlist)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"batch_guard: shortlist not found: {path}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print(f"batch_guard: shortlist is not valid JSON ({e}): {path}", file=sys.stderr)
        return 2

    verdict = check_batch(data, auto_approve=args.auto_approve or None, cap=args.cap)
    label = "OK" if verdict.allowed else "REFUSED"
    print(f"batch_guard: {label} - {verdict.reason}")
    if verdict.allowed and verdict.auto_approve:
        print(f"batch_guard: hands-off gates still enforced for: {', '.join(sorted(NEVER_SKIPPABLE))}")
    return verdict.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
