"""Candidate profile — the single place this platform learns whose search it is running.

Everything career-shaped used to be a module-level constant: the commute band, the target and
excluded titles, the CV keyword list, the seniority band, and — worst — the *candidate's biography*,
which the quality gates treated as ground truth. That made the engine usable by exactly one person.
A second user's **true** "MongoDB" or "Go" line was reported as a false claim, and every role
outside one country was silently dropped before scoring.

This module replaces all of it with one JSON file, so the logic adapts to the person instead of the
person having to match the logic.

**It also calls :func:`load_dotenv` — once, here.** Before this existed the README claimed
"everything is driven by `.env`" while nothing in ``src/`` ever read it: ``TARGET_ROLES``,
``PREFERRED_LOCATIONS``, ``HOME_CITY``, ``REJECT_TITLES``, ``MIN_KEYWORD_SCORE`` and ``CV_PATH``
were read by no code at all. Editing `.env` changed nothing.

Resolution order for the profile file:
  1. an explicit ``path`` argument / ``--profile`` flag
  2. ``$PROFILE_DIR/profile.json``   ← the normal case; PROFILE_DIR lives OUTSIDE this repo
  3. ``./profile.json``

``PROFILE_DIR`` is deliberately outside the repo and gitignored: two people share this platform,
and neither one's CV, pipeline, salary bands or rejections belong in the other's clone.

CLI::

    uv run python -m src.jobcore.profile show       # the resolved profile as JSON
    uv run python -m src.jobcore.profile validate   # exit 1 with reasons if malformed
    uv run python -m src.jobcore.profile --brief    # compact summary to paste into a skill prompt
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .console import force_utf8_output

try:  # python-dotenv is a declared dependency, but never fail closed on it.
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - exercised only on a broken install
    # Deliberately a permissive stub, not a signature match: its only job is to let the module
    # import on a broken install. mypy is right that it differs from the real function; making
    # them identical would mean restating python-dotenv's full signature here, which would then
    # silently rot against it.
    def load_dotenv(*_a, **_kw):  # type: ignore[misc]
        return False


SCHEMA_VERSION = "1.0"

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATE_PATH = REPO_ROOT / "profiles" / "_template.json"

GEO_MODES = ("required", "preferred")
#: Where a profile lives when the user has not said otherwise.
DEFAULT_BASENAME = "profile.json"

_REQUIRED_SECTIONS = ("identity", "locale", "geo", "roles", "skills")


class ProfileError(RuntimeError):
    """Raised when a profile cannot be found or is unusable."""


@dataclass
class Profile:
    """A validated candidate profile.

    Regexes are compiled ONCE here rather than at each call site: a profile is loaded per process,
    but ``is_relevant`` runs per job across thousands of rows.
    """

    raw: dict
    path: Path | None = None
    _compiled: dict = field(default_factory=dict, repr=False)

    # --- plain accessors ----------------------------------------------------
    @property
    def name(self) -> str:
        return self.raw.get("identity", {}).get("name", "")

    @property
    def timezone(self) -> str:
        return self.raw.get("locale", {}).get("timezone", "UTC")

    @property
    def currency(self) -> str:
        return self.raw.get("locale", {}).get("currency", "USD")

    @property
    def languages(self) -> list[str]:
        return list(self.raw.get("locale", {}).get("languages", ["en"]))

    @property
    def geo_mode(self) -> str:
        return self.raw.get("geo", {}).get("mode", "preferred")

    @property
    def preferred_locations(self) -> list[str]:
        return [s.lower() for s in self.raw.get("geo", {}).get("preferred_locations", [])]

    @property
    def commute_extra(self) -> dict:
        return dict(self.raw.get("geo", {}).get("commute_extra", {}))

    @property
    def require_leadership(self) -> bool:
        return bool(self.raw.get("roles", {}).get("require_leadership", False))

    @property
    def keywords(self) -> list[str]:
        return [s.lower() for s in self.raw.get("skills", {}).get("keywords", [])]

    @property
    def boost_keywords(self) -> list[str]:
        return [s.lower() for s in self.raw.get("skills", {}).get("boost_keywords", [])]

    @property
    def facts(self) -> dict:
        return dict(self.raw.get("facts", {}))

    @property
    def has_facts(self) -> bool:
        """Whether biography-dependent gates can run at all.

        A profile with no ``facts`` is legitimate — it just means the honesty gates have nothing to
        check against. Those gates must then **SKIP and say so**, never silently pass. A skipped
        gate is not a green gate.
        """
        f = self.facts
        return any(f.get(k) for k in ("known_languages", "knockout_languages", "employers", "never_claim"))

    @property
    def salary(self) -> dict:
        return dict(self.raw.get("salary", {}))

    @property
    def private_terms(self) -> list[str]:
        """Words/phrases that must never appear in candidate-facing text (``privacy.never_mention``)."""
        return [t.strip() for t in (self.raw.get("privacy") or {}).get("never_mention", []) if t and t.strip()]

    @property
    def private_dates(self) -> list[str]:
        """ISO dates (``YYYY-MM-DD``) that must never appear in candidate-facing text."""
        return [d.strip() for d in (self.raw.get("privacy") or {}).get("private_dates", []) if d and d.strip()]

    @property
    def has_privacy_rules(self) -> bool:
        return bool(self.private_terms or self.private_dates)

    # --- compiled patterns --------------------------------------------------
    def pattern(self, key: str) -> re.Pattern | None:
        """Compiled alternation for a profile word-list, or None when the list is empty.

        None means "this filter is not configured" — callers must treat that as *no opinion*,
        not as *match nothing*.
        """
        if key in self._compiled:
            return self._compiled[key]
        words = _lookup_list(self.raw, key)
        pat = _compile_alternation(words)
        self._compiled[key] = pat
        return pat

    def brief(self) -> str:
        """A compact, token-cheap summary for pasting into a skill prompt."""
        r = self.raw.get("roles", {})
        lines = [
            f"CANDIDATE PROFILE ({self.path or 'defaults'})",
            f"  name/tz/currency : {self.name} · {self.timezone} · {self.currency}",
            f"  languages        : {', '.join(self.languages)}",
            f"  geo ({self.geo_mode:9s}): {', '.join(self.preferred_locations) or '(any)'}",
            f"  target titles    : {', '.join(r.get('target_titles', [])) or '(any)'}",
            f"  exclude titles   : {', '.join(r.get('exclude_titles', [])) or '(none)'}",
            f"  leadership req'd : {self.require_leadership}",
            f"  keywords         : {', '.join(self.keywords[:12])}{' …' if len(self.keywords) > 12 else ''}",
        ]
        f = self.facts
        if self.has_facts:
            lines += [
                f"  known languages  : {', '.join(f.get('known_languages', [])) or '(none)'}",
                f"  NEVER claim      : {', '.join(f.get('never_claim', [])) or '(none)'}",
                f"  employers        : {', '.join(e.get('name', '?') for e in f.get('employers', []))}",
            ]
        else:
            lines.append("  facts            : NOT CONFIGURED — honesty gates will SKIP, not pass")
        lines.append(
            f"  privacy firewall : {len(self.private_terms)} term(s), {len(self.private_dates)} date(s)"
            if self.has_privacy_rules
            else "  privacy firewall : none configured (privacy.never_mention / private_dates are empty)"
        )
        sal = self.salary
        if sal:
            lines.append(
                f"  salary           : open {sal.get('open')} / target {sal.get('target')} / "
                f"floor {sal.get('floor')} {sal.get('currency', self.currency)} per {sal.get('period', 'month')}"
            )
        return "\n".join(lines)


# --- helpers -----------------------------------------------------------------

#: Dotted paths for the word-lists that become regexes.
_LIST_KEYS = {
    "target_titles": ("roles", "target_titles"),
    "exclude_titles": ("roles", "exclude_titles"),
    "above_band_titles": ("roles", "above_band_titles"),
    "in_band_titles": ("roles", "in_band_titles"),
    "depth_risk_titles": ("roles", "depth_risk_titles"),
    "domain_label_exclusions": ("roles", "domain_label_exclusions"),
    "domain_exclusions": ("roles", "domain_exclusions"),
    "domain_exclusion_overrides": ("roles", "domain_exclusion_overrides"),
    "never_claim": ("facts", "never_claim"),
    "knockout_languages": ("facts", "knockout_languages"),
    "known_languages": ("facts", "known_languages"),
}


def _lookup_list(raw: dict, key: str) -> list[str]:
    section, name = _LIST_KEYS.get(key, (None, None))
    if section is None:
        raise KeyError(f"unknown profile list {key!r} — known: {', '.join(sorted(_LIST_KEYS))}")
    return list(raw.get(section, {}).get(name, []))


def _compile_alternation(words: list[str]) -> re.Pattern | None:
    """Word-boundary alternation over a config list, or None if empty.

    Multi-word phrases keep their spacing flexible so "team lead" also matches "team  lead".
    """
    cleaned = [w.strip() for w in words if w and w.strip()]
    if not cleaned:
        return None
    parts = [_hebrew_prefixed(r"\s+".join(re.escape(tok) for tok in w.split()), w) for w in cleaned]
    return re.compile(r"\b(" + "|".join(parts) + r")\b", re.I)


_HEBREW = re.compile(r"[\u0590-\u05FF]")
#: Hebrew attaches one-letter prefixes to the word ("ב-Python", "המפתח", "למנהל"): ו, ה, ב, כ, ל, מ, ש.
_HEBREW_PREFIX = "(?:[והבכלמש]{1,2}-?)?"


def _hebrew_prefixed(pattern: str, original: str) -> str:
    """Let a Hebrew keyword also match with its attached prefix letters (מפתח -> למפתח, המפתח)."""
    return _HEBREW_PREFIX + pattern if _HEBREW.search(original) else pattern


def _skeleton() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "identity": {"name": "", "cv_path": "cv.md"},
        "locale": {"timezone": "UTC", "currency": "USD", "languages": ["en"]},
        "geo": {"mode": "preferred", "preferred_locations": [], "commute_extra": {}},
        "roles": {
            "target_titles": [],
            "exclude_titles": [],
            "in_band_titles": [],
            "above_band_titles": [],
            "require_leadership": False,
            "domain_exclusions": [],
            "domain_exclusion_overrides": [],
            "depth_risk_titles": [],
            "domain_label_exclusions": [],
        },
        "skills": {"keywords": [], "boost_keywords": []},
        "facts": {},
        "salary": {},
        # The RAG lane. `corpus_globs` are resolved relative to PROFILE_DIR so your study notes
        # stay outside the shared repo. Both empty is a valid "I don't use the RAG lane" —
        # the gate then reports SKIPPED (exit 1), never a vacuous PASS.
        "rag": {"corpus_globs": [], "goldens": []},
        # The CSV ingest lane. These were hardcoded to one job-board's export ("Referally
        # Leadership*.csv", sender must contain "Referally"), so a second user pointing the
        # shortlist at their own CSV got zero rows and NO error — the worst failure shape.
        "csv": {"pattern": "", "sender_filter": ""},
        # Things that must NEVER reach a CV, cover letter or outreach draft: a pending legal or HR
        # process, a manager's name, an exact salary figure. Checked deterministically by
        # ``gates.find_private_scope_leak``. Empty means "no firewall configured" — not "all clear".
        "privacy": {"never_mention": [], "private_dates": []},
        # Research depth for the company-researcher skill. `depth` is the default; the two
        # override lists force a company either way. Skills referenced `RESEARCH_DEPTH`,
        # `DEEP_RESEARCH_COMPANIES` and `QUICK_RESEARCH_COMPANIES` from `.env` — keys no code
        # has read since the profile refactor, so the instruction was unfollowable.
        "research": {"depth": "quick", "deep_companies": [], "quick_companies": []},
    }


# --- load / validate ----------------------------------------------------------


def _normalize_dir(raw: str) -> Path:
    """Expand ``~`` and translate an MSYS/Git-Bash path into something Windows Python can open.

    On Windows, ``echo "PROFILE_DIR=$HOME/x" >> .env`` inside Git Bash writes ``/c/Users/…``.
    Python then resolves that to ``C:\\c\\Users\\…``, the profile is silently not found, and the
    tools quietly fall back to defaults — scoring everything flat with no error. Failing to find
    a profile is meant to be loud, so translate the path instead of losing it.

    **The Windows guard is not cosmetic.** On Linux ``/c/Users/foo`` is a perfectly ordinary
    absolute path, and rewriting it to ``C:/Users/foo`` turns it into a RELATIVE path resolved
    against the cwd. A Linux user with a ``/c`` or ``/d`` mount would have their profile silently
    not found — the exact failure this function exists to prevent, inflicted on the other
    platform. Caught by the first CI run on Linux; invisible in every local run, because every
    local run was on Windows.
    """
    return Path(translate_msys_path(str(raw), platform=sys.platform)).expanduser()


def translate_msys_path(raw: str, *, platform: str) -> str:
    """The pure string half of :func:`_normalize_dir`. Separated so it is testable anywhere.

    ``platform`` is an explicit parameter rather than a read of ``sys.platform`` because this
    logic is platform-CONDITIONAL, and platform-conditional code tested only on the platform you
    happen to be sitting at is how the Linux bug shipped in the first place.

    It returns a ``str``, not a ``Path``, on purpose. ``Path`` is platform-bound —
    ``PosixPath("C:/Users/foo").drive`` is ``""`` on Linux — so no assertion made through
    ``Path`` can distinguish the two branches from both sides. The first attempt at a
    cross-platform test did exactly that and passed vacuously on Linux; its own
    "do the branches differ?" guard is what caught it.
    """
    s = raw.strip().strip('"').strip("'")
    if platform != "win32":
        return s
    m = re.fullmatch(r"/([a-zA-Z])(/.*)?", s)
    if m:  # /c/Users/foo  ->  C:/Users/foo
        return f"{m.group(1).upper()}:{m.group(2) or '/'}"
    return s


def resolve_path(path: str | Path | None = None) -> Path | None:
    """Where the profile should be read from, or None if nothing is configured."""
    if path:
        return Path(path)
    env_dir = os.environ.get("PROFILE_DIR")
    if env_dir:
        return _normalize_dir(env_dir) / DEFAULT_BASENAME
    local = Path.cwd() / DEFAULT_BASENAME
    return local if local.exists() else None


def load(path: str | Path | None = None, *, required: bool = False) -> Profile:
    """Load and validate a profile.

    ``load_dotenv`` runs first so ``PROFILE_DIR`` can come from ``.env`` rather than only the
    shell — the fix for the config surface that was previously decorative.

    With ``required=False`` (the default) a missing profile yields an empty one rather than
    raising, so the deterministic tools still run on a fresh clone; the gates that need facts
    will report themselves as skipped.
    """
    load_dotenv(REPO_ROOT / ".env", override=False)
    p = resolve_path(path)
    if p is None or not p.exists():
        if required:
            raise ProfileError(
                f"no profile found (looked at {p or 'PROFILE_DIR, ./profile.json'}). "
                f"Copy profiles/_template.json to $PROFILE_DIR/profile.json to start."
            )
        return Profile(raw=_skeleton(), path=None)
    try:
        raw = json.loads(Path(p).read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ProfileError(f"{p} is not valid JSON: {e}") from e
    merged = _skeleton()
    for section, value in raw.items():
        if isinstance(value, dict) and isinstance(merged.get(section), dict):
            merged[section].update(value)
        else:
            merged[section] = value
    return Profile(raw=merged, path=Path(p))


def validate(profile: Profile) -> list[str]:
    """Return human-readable problems; empty list means usable."""
    problems: list[str] = []
    raw = profile.raw

    if raw.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version is {raw.get('schema_version')!r}, expected {SCHEMA_VERSION!r}")
    for section in _REQUIRED_SECTIONS:
        if not isinstance(raw.get(section), dict):
            problems.append(f"missing or malformed section {section!r}")

    if profile.geo_mode not in GEO_MODES:
        problems.append(f"geo.mode is {profile.geo_mode!r}, must be one of {', '.join(GEO_MODES)}")
    if profile.geo_mode == "required" and not profile.preferred_locations:
        problems.append("geo.mode is 'required' but geo.preferred_locations is empty — this drops every role")

    if not profile.name:
        problems.append("identity.name is empty — the PDF filename and outreach depend on it")
    if not profile.keywords:
        problems.append("skills.keywords is empty — every role would score the same")

    for key in _LIST_KEYS:
        try:
            profile.pattern(key)
        except re.error as e:  # a list entry that breaks compilation
            problems.append(f"roles/facts list {key!r} does not compile: {e}")

    boost = set(profile.boost_keywords)
    if boost and not boost.issubset(set(profile.keywords)):
        problems.append(
            f"skills.boost_keywords not a subset of skills.keywords: {sorted(boost - set(profile.keywords))}"
        )

    for emp in profile.facts.get("employers", []):
        if not isinstance(emp, dict) or not emp.get("name"):
            problems.append(f"facts.employers entry missing 'name': {emp!r}")

    # A corpus with no goldens is the config that produced "PASS goldens 0/0" — a blocking gate
    # asserting nothing. The gate now refuses it at runtime; flag it here so it's caught at
    # `profile validate` time instead of at push time.
    rag = raw.get("rag") or {}
    if rag.get("corpus_globs") and not rag.get("goldens"):
        problems.append(
            "rag.corpus_globs is set but rag.goldens is empty — the retrieval gate cannot "
            "assert anything and will report SKIPPED (non-zero). Add goldens or clear the globs."
        )

    # Same silent-empty trap as the RAG gate, one lane over: a pattern that matches nothing
    # yields an empty shortlist that looks like "no good roles this week".
    depth = (raw.get("research") or {}).get("depth", "quick")
    if depth not in ("quick", "deep"):
        problems.append(f"research.depth is {depth!r}, must be 'quick' or 'deep'")

    csv_cfg = raw.get("csv") or {}
    if csv_cfg.get("sender_filter") and not csv_cfg.get("pattern"):
        problems.append("csv.sender_filter is set but csv.pattern is empty — no CSV will ever be found")

    for d in profile.private_dates:
        try:
            date.fromisoformat(d)
        except ValueError:
            problems.append(f"privacy.private_dates entry {d!r} is not an ISO date (YYYY-MM-DD)")

    return problems


# --- CLI ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    force_utf8_output()
    ap = argparse.ArgumentParser(description="Inspect and validate the candidate profile.")
    ap.add_argument("cmd", nargs="?", default="show", choices=["show", "validate", "path"])
    ap.add_argument("--profile", default=None, help="Explicit profile.json path")
    ap.add_argument("--brief", action="store_true", help="Compact summary for a skill prompt")
    args = ap.parse_args(argv)

    try:
        prof = load(args.profile)
    except ProfileError as e:
        print(f"profile: {e}", file=sys.stderr)
        return 1

    if args.brief:
        print(prof.brief())
        return 0
    if args.cmd == "path":
        print(prof.path or "(none configured)")
        return 0
    if args.cmd == "validate":
        problems = validate(prof)
        if problems:
            for p in problems:
                print(f"profile: {p}", file=sys.stderr)
            return 1
        print(f"profile: OK ({prof.path or 'defaults'})", file=sys.stderr)
        return 0
    print(json.dumps(prof.raw, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
