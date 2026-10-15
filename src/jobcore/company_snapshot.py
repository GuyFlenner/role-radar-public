"""``positions/<slug>/profile.json`` — the company snapshot, and the refusal that makes it servable.

Why a sidecar at all: the research markdown next to it has no schema. Two ``COMPANY_RESEARCH.md``
files in this repo share **zero** H2 headings, the same concept appears as ``## Snapshot`` /
``## Company Snapshot`` / ``## Company Overview`` / ``## What the business does``, and ``job-apply``
already fuzzy-matches those headings to read a Glassdoor rating and count red flags — finding
nothing, silently, on any file that names its sections differently. A closed schema in a real data
format is the fix: a missing field is *absent*, not *zero*.

Why the refusal: the folder this file sits in also holds salary walk-away floors, named-interviewer
dossiers with handling advice, honest self-assessed gaps, and clearance status. None of that may
reach a browser. So :func:`validate_profile` is a control, and it refuses on three axes:

1. **Unknown key** — the schema is closed, not open. A field nobody declared is a field nobody
   reviewed for sensitivity.
2. **Sensitive key name** — ``salary``, ``interviewer``, ``phone`` and friends.
3. **Sensitive value** — because (2) alone is security theatre. The realistic leak is a
   compensation floor pasted into ``what_they_do``, which no key denylist would ever see.

Rule (3) is deliberately tuned to compensation rather than "numbers": a filter that fired on
``founded 2005`` or ``190+ countries`` would be switched off within a week, and a control that gets
switched off protects nothing.

Deterministic: zero model calls, stdlib only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .console import force_utf8_output

SCHEMA_VERSION = "1.0"

PROFILE_FILENAME = "profile.json"

#: The complete, closed set of fields a servable profile may carry. Everything here is the kind of
#: fact a company puts in its own about-page — nothing derived from a private conversation.
ALLOWED_FIELDS = (
    "schema_version",
    "company",
    "industry",
    "founded",
    "size",
    "funding",
    "what_they_do",
    "differentiator",
    "tech_stack",
    "sources",
    "updated",
)

#: Whole *tokens* that disqualify a key name. Matched against the key split on non-alphanumerics,
#: never as a raw substring — ``"comp" in "company"`` is true, and a control that rejects the
#: ``company`` field is a control somebody deletes on its first day.
SENSITIVE_KEY_TOKENS = frozenset(
    {
        "salary",
        "salaries",
        "comp",
        "compensation",
        "pay",
        "package",
        "band",
        "bands",
        "floor",
        "ceiling",
        "equity",
        "rsu",
        "rsus",
        "options",
        "interviewer",
        "interviewers",
        "recruiter",
        "recruiters",
        "headhunter",
        "contact",
        "contacts",
        "phone",
        "email",
        "emails",
        "clearance",
        "debrief",
        "debriefs",
        "note",
        "notes",
        "private",
        "secret",
    }
)

#: Key shapes a single token cannot express (``walk_away`` splits into two innocuous words).
SENSITIVE_KEY_PATTERNS = (
    re.compile(r"walk[\s_-]?away", re.I),
    re.compile(r"head[\s_-]?hunter", re.I),
)


def _sensitive_key(key: str) -> str | None:
    """Why this key name is unsafe, or ``None``."""
    low = str(key).lower()
    for pat in SENSITIVE_KEY_PATTERNS:
        if pat.search(low):
            return pat.pattern
    for token in re.split(r"[^a-z0-9]+", low):
        if token in SENSITIVE_KEY_TOKENS:
            return token
    return None


#: The type each declared field may hold. Enforced, because "it's just prose" is how a nested
#: object smuggles a salary past a scanner that only walks strings and lists. A field whose value
#: is the wrong shape is refused outright rather than inspected.
FIELD_TYPES: dict[str, type | tuple[type, ...]] = {
    "schema_version": str,
    "company": str,
    "industry": str,
    "founded": int,
    "size": str,
    "funding": str,
    "what_they_do": str,
    "differentiator": str,
    "tech_stack": list,
    "sources": list,
    "updated": str,
}

#: Value patterns that mean "this is compensation or a negotiation position", wherever they appear.
#: Currency-, K- or phrase-anchored on purpose — see the module docstring on why a bare
#: number-matcher would be worse than nothing. Over-refusing here is far cheaper than a leak, but
#: a filter that fires on "founded 2005" or "1,871-2,500 employees" gets switched off, so every
#: numeric pattern needs a compensation anchor (a K suffix, comma-thousands on BOTH sides, or a
#: currency word).
#: A magnitude written the way money is: "45k", "45 thousand".
_K = r"\d{2,3}\s*(?:k|thousand)\b"
#: Range separators people actually type, including "and" ("between 45 and 50k").
_SEP = r"(?:\s*[–—\-]\s*|\s+to\s+|\s+and\s+)"
#: Words that make a nearby number *money* rather than scale.
_MONEY_WORD = (
    r"salar(?:y|ies)|pay|paid|compensation|comp|base|offers?|offered|floor|ceiling|package|"
    r"band|budget|earns?|earnings?|gross|net|monthly|per\s+month|a\s+month|up\s+to|starting\s+at|between"
)
#: …and the nouns that make it scale rather than money. A company legitimately says "supports up to
#: 100k concurrent users"; refusing that would make authors switch the control off, which is the
#: failure mode this whole module is written against. Allows two intervening words
#: ("100k *concurrent* users").
_SCALE_NOUN = (
    r"users?|customers?|businesses|clients?|merchants?|sellers?|buyers?|transactions?|payments?|"
    r"requests?|events?|records?|rows?|employees?|seats?|downloads?|installs?|developers?|"
    r"companies|organi[sz]ations?|accounts?|countries|markets?|integrations?|partners?"
)
_NOT_SCALE = rf"(?!(?:\s+\w+){{0,2}}\s+(?:{_SCALE_NOUN})\b)"

SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"₪"),
    re.compile(r"\bNIS\b", re.I),
    re.compile(r"\bILS\b", re.I),
    re.compile(r"\bshekels?\b", re.I),
    re.compile(r"\bsalar(?:y|ies)\b", re.I),
    re.compile(r"\bwalk[\s_-]?away\b", re.I),
    re.compile(r"\bcomp(?:ensation)?\s+(?:band|range)\b", re.I),
    # A compensation band, magnitude-anchored on either side or both: "45-50K", "45K-50K", "45K-50",
    # "45 to 50k", "between 45 and 50k", "45–50 thousand".
    re.compile(rf"\d{{2,3}}\s*(?:k|thousand)?{_SEP}{_K}", re.I),
    re.compile(rf"{_K}{_SEP}\d{{2,3}}\b", re.I),
    # Comma-thousands on BOTH sides: "45,000-50,000". Two-to-three digits before the comma keeps
    # "1,871-2,500 employees" out of it.
    re.compile(rf"\d{{2,3}},\d{{3}}\s*{_SEP}\s*\d{{2,3}},\d{{3}}"),
    # A SINGLE figure is compensation when a money word sits near it — "floor is 45k",
    # "up to 60k", "base 50k + equity", "offered 50k". The original pattern required the digits to
    # follow "floor" almost immediately, so "floor is 45k" — a one-word rephrase of the exact leak
    # this module was written to stop — walked straight through.
    re.compile(rf"\b(?:{_MONEY_WORD})\b[^.\n]{{0,30}}?{_K}{_NOT_SCALE}", re.I),
    re.compile(rf"{_K}[^.\n]{{0,20}}?\b(?:base|gross|net|salary|monthly|per\s+month|package|ctc)\b", re.I),
    # The same money word next to a comma-thousands figure — "floor is around 45,000". The scale
    # guard keeps "up to 250,000 customers" clean.
    re.compile(rf"\b(?:{_MONEY_WORD})\b[^.\n]{{0,30}}?\b\d{{2,3}},\d{{3}}\b{_NOT_SCALE}", re.I),
    # The same, written without a magnitude suffix: "monthly 48000", "48,000 per month".
    re.compile(r"\b(?:monthly|per\s+month|a\s+month)\b[^.\n]{0,15}?\b\d{4,6}\b", re.I),
    re.compile(r"\b\d{2,3},?\d{3}\b\s*(?:a month|per month|/mo|monthly)", re.I),
)


def _sensitive_value(value: object) -> str | None:
    """The first sensitive pattern found anywhere in ``value``, or ``None``.

    Walks **into dicts as well as lists**, and checks dict keys as well as values. A scanner that
    only descends into lists is defeated by ``{"differentiator": {"x": "walk away below 32,000"}}``
    — one level of nesting, no adversarial effort required. :data:`FIELD_TYPES` refuses that shape
    anyway; this is the second lock on the same door.
    """
    if isinstance(value, str):
        for pat in SENSITIVE_VALUE_PATTERNS:
            if pat.search(value):
                return pat.pattern
    elif isinstance(value, dict):
        for key, item in value.items():
            hit = _sensitive_value(key) or _sensitive_value(item)
            if hit:
                return hit
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            hit = _sensitive_value(item)
            if hit:
                return hit
    return None


def validate_profile(data: object) -> list[str]:
    """Return human-readable problems; an empty list means the profile is safe to serve.

    Never raises — a malformed profile is a problem to report, not an exception to handle at every
    call site.
    """
    problems: list[str] = []

    if not isinstance(data, dict):
        return [f"profile must be a JSON object, got {type(data).__name__}"]

    for key, value in data.items():
        if key not in ALLOWED_FIELDS:
            problems.append(f"unknown field {key!r} — the profile schema is closed; declare it or drop it")
            # Still fall through: an undeclared field carrying a salary should say so too.

        bad_key = _sensitive_key(key)
        if bad_key:
            problems.append(f"field {key!r} has a sensitive name ({bad_key!r}) — this file is served to a browser")

        hit = _sensitive_value(value)
        if hit:
            problems.append(
                f"field {key!r} contains sensitive content (matched {hit!r}) — "
                f"compensation and negotiation positions never go in a servable profile"
            )

    # Shape enforcement. This is a security control, not tidiness: a declared field holding a
    # nested object is how compensation gets past a scanner, so the wrong shape is refused rather
    # than inspected and allowed.
    for field, expected in FIELD_TYPES.items():
        if field not in data:
            continue
        value = data[field]
        # bool is a subclass of int; a `founded: true` must not read as a valid year.
        if expected is int and isinstance(value, bool):
            problems.append(f"{field} must be a year as an integer, got {value!r}")
        elif not isinstance(value, expected):
            problems.append(f"{field} must be {getattr(expected, '__name__', expected)}, got {type(value).__name__}")
        elif isinstance(value, list) and not all(isinstance(item, str) for item in value):
            problems.append(f"{field} must be a list of strings")

    if "company" in data and isinstance(data["company"], str) and not data["company"].strip():
        problems.append("company is empty")

    return problems


def profile_path(folder: Path | str) -> Path:
    """Where the profile lives for a given position folder."""
    return Path(folder) / PROFILE_FILENAME


def load_profile(folder: Path | str) -> tuple[dict | None, list[str]]:
    """Read and validate ``<folder>/profile.json``. Returns ``(profile_or_None, problems)``.

    A missing file is normal and clean — most positions have no profile yet. Anything that fails
    validation returns ``None``, never the raw content: the caller must not be able to serve
    something this function judged unsafe.
    """
    path = profile_path(folder)
    try:
        if not path.is_file():
            return None, []
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, [f"could not read {PROFILE_FILENAME}: {exc}"]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, [f"could not parse {PROFILE_FILENAME} as JSON: {exc}"]

    problems = validate_profile(data)
    if problems:
        return None, problems
    return data, []


# --- coverage report ---------------------------------------------------------


def survey(ledger: dict) -> list[dict]:
    """One row per tracked position that has a folder: does it have a servable profile?

    Exists because nothing backfills ``profile.json`` — the 47 existing folders have none until
    ``company-researcher`` runs again, and an invisible rollout is one that silently never happens.
    Returns dicts of ``{id, company, folder, state, detail}`` where ``state`` is one of
    ``ok`` / ``missing`` / ``refused``.
    """
    out: list[dict] = []
    for opp in ledger.get("opportunities", []):
        folder = opp.get("position_folder")
        if not folder:
            continue
        prof, problems = load_profile(folder)
        if prof is not None:
            state, detail = "ok", prof.get("updated", "")
        elif problems:
            # A refused profile is the interesting case: the file exists, so somebody wrote it, and
            # it is being withheld from the board rather than shown.
            state, detail = "refused", "; ".join(problems)
        else:
            state, detail = "missing", ""
        out.append(
            {
                "id": opp.get("id", ""),
                "company": opp.get("company", ""),
                "folder": folder,
                "state": state,
                "detail": detail,
            }
        )
    return out


def main(argv: list[str] | None = None) -> int:
    """``uv run python -m src.jobcore.company_snapshot status`` — which folders have a servable profile.

    Exit ``0`` when nothing is refused, ``1`` when at least one existing ``profile.json`` fails
    validation. Missing profiles are *not* an error: most positions simply have not been researched
    since the schema landed.
    """
    force_utf8_output()
    import argparse
    import sys

    from .pipeline import LEDGER_DEFAULT, load

    # The summary line uses "·", and Windows consoles default to cp1252 — the same trap that turned
    # `src.jobcore.usage` into a UnicodeEncodeError traceback, and that crashed the drawio lint
    # while it was printing a finding. Degrading a character beats losing the report.
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
    except (AttributeError, OSError):  # pragma: no cover - non-reconfigurable stream
        pass

    ap = argparse.ArgumentParser(description=(main.__doc__ or "").splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_status = sub.add_parser("status", help="report profile.json coverage across tracked folders")
    p_status.add_argument("--ledger", default=str(LEDGER_DEFAULT))
    p_status.add_argument("--missing", action="store_true", help="list the folders with no profile too")
    args = ap.parse_args(argv)

    rows = survey(load(args.ledger))
    by_state: dict[str, list[dict]] = {"ok": [], "missing": [], "refused": []}
    for r in rows:
        by_state[r["state"]].append(r)

    for r in by_state["refused"]:
        print(f"REFUSED  {r['id']}  {r['folder']}\n         {r['detail']}")
    if args.missing:
        for r in by_state["missing"]:
            print(f"missing  {r['id']}  {r['folder']}")

    print(
        f"profile coverage: {len(by_state['ok'])} ok · {len(by_state['missing'])} missing · "
        f"{len(by_state['refused'])} refused  (of {len(rows)} tracked folders)"
    )
    if by_state["missing"] and not args.missing:
        print("run with --missing to list the folders with no profile yet")
    return 1 if by_state["refused"] else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
