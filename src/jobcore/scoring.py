"""Position relevance + match scoring — deterministic, no LLM, driven by the candidate profile.

The *mechanism* here is generic: count skill-keyword hits, floor titles in the candidate's target
band, drop titles and domains they have ruled out. The *word lists* are not generic and never
were — they come from :mod:`src.jobcore.profile`.

Before that split this module compiled one person's career into the logic: an 18-city Israeli
commute band used as a hard drop, a keyword list for one specific backend/AI stack, and a
``KEEP_TITLES`` gate that required a leadership title — so a frontend IC in Berlin was discarded
before scoring and, had it survived, scored 1.

**No profile configured ⇒ no opinion.** An empty profile makes :func:`is_relevant` permissive and
scores flat, rather than silently filtering against somebody else's career. That state is a
misconfiguration, and ``python -m src.jobcore.profile validate`` names it.

`score` floors target-band + AI/ML titles high so the keyword stage never discards them before an
LLM filter re-evaluates (the historical "LLM Team Lead scored 2/10" false negative).
"""

from __future__ import annotations

import re

from .profile import Profile, load

# Generic English title vocabulary — not career-specific, so it stays in code.
# Whether a leadership title *matters* is `roles.require_leadership` in the profile.
_LEADERSHIP_TITLE = re.compile(r"\b(lead|leader|manager|head|director|principal|staff)\b", re.I)
_AI_TITLE = re.compile(r"\b(ai|llm|ml|genai|gen-ai|agent|agentic|nlp|machine\s+learning|data\s+scien)\b", re.I)

# Generic "this is a software role" signal, paired with a leadership word to catch target titles
# an explicit profile list missed.
_SOFTWARE_SIGNAL = re.compile(r"software|engineering|r&d|data\s+engineer|mlops|backend|fullstack|devops|sre", re.I)

# Department/domain labels meaning "not a software role" — the DEFAULT, for a software search.
# Matched as TOKENS anywhere in the label, never anchored to the whole string: job boards label
# compositionally ("R&D - Hardware", "R&D - Data"), and an anchored match only ever fired on a bare
# "Hardware" — so the gate went quiet the moment a poll moved from a handful of boards to dozens
# whose labels were richer. ``operations`` is deliberately absent: it spans business-ops AND
# SRE/production engineering, and dropping the SRE sense would lose real seats.
# A candidate whose search is NOT software overrides this via ``roles.domain_label_exclusions``.
_DOMAIN_LABEL_DEFAULT = (
    "qa",
    "quality assurance",
    "hardware",
    "mechanical",
    "electrical",
    "firmware",
    "embedded",
    "product management",
    "it",
    "sysadmin",
    "helpdesk",
    "human resources",
    "hr",
    "recruiting",
    "recruitment",
    "talent",
    "sales",
    "marketing",
    "finance",
    "accounting",
    "legal",
    "logistics",
    "facilities",
    "manufacturing",
    "customer success",
    "customer support",
    "customer experience",
    "customer service",
)
_DOMAIN_LABEL_DEFAULT_RE = re.compile(
    r"\b(" + "|".join(r"\s+".join(re.escape(t) for t in w.split()) for w in _DOMAIN_LABEL_DEFAULT) + r")\b", re.I
)

_DEFAULT: Profile | None = None


def default_profile() -> Profile:
    """The process-wide profile, loaded once from PROFILE_DIR / ./profile.json."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = load()
    return _DEFAULT


def set_default_profile(profile: Profile | None) -> None:
    """Override the process-wide profile (used by tests and the CLIs' ``--profile`` flag)."""
    global _DEFAULT
    _DEFAULT = profile


def _p(profile: Profile | None) -> Profile:
    return profile if profile is not None else default_profile()


def location_allowed(job: dict, profile: Profile | None = None) -> tuple[bool, bool]:
    """``(allowed, is_preferred)`` for a job's location.

    ``allowed`` is what a caller may filter on; ``is_preferred`` is a display flag. Under
    ``geo.mode == "preferred"`` everything is allowed and only the flag varies — which is what
    makes this platform usable outside a single commute band.
    """
    prof = _p(profile)
    prefs = prof.preferred_locations
    loc = (job.get("location") or "").lower()
    extra = [str(x).lower() for x in (job.get("commute_extra") or [])]
    is_preferred = any(c in loc for c in prefs) if prefs else False
    if not prefs or prof.geo_mode == "preferred":
        return True, is_preferred
    return (is_preferred or any(c in loc for c in extra)), is_preferred


def is_relevant(job: dict, profile: Profile | None = None) -> bool:
    """Whether a job is worth scoring at all.

    Order matters: explicit exclusions beat explicit inclusions, so a profile can rule out a
    title even when it also matches a target pattern.
    """
    prof = _p(profile)
    t = (job.get("title") or "").lower()
    d = (job.get("domain") or "").lower()

    excl = prof.pattern("exclude_titles")
    if excl and excl.search(t):
        return False

    dom_ex = prof.pattern("domain_exclusions")
    dom_ok = prof.pattern("domain_exclusion_overrides")
    if dom_ex and dom_ex.search(t) and not (dom_ok and dom_ok.search(t)):
        return False

    if d:
        label_ex = prof.pattern("domain_label_exclusions") or _DOMAIN_LABEL_DEFAULT_RE
        if label_ex.search(d):
            return False

    keep = prof.pattern("target_titles")
    if keep and keep.search(t):
        return True

    # Fall back to the generic "leadership + software" shape only when the profile wants a
    # leadership role. An IC search must not need a lead title to be considered relevant.
    if prof.require_leadership:
        return bool(re.search(r"\b(lead|manager|director)\b", t) and _SOFTWARE_SIGNAL.search(t))

    # No target list at all: no opinion, so don't filter. With a target list that missed, fall
    # back to the generic software signal.
    return True if keep is None else bool(_SOFTWARE_SIGNAL.search(t))


def level_band(job: dict, profile: Profile | None = None) -> str:
    """Classify a role's altitude: ``"above_band"`` or ``"in_band"``.

    Deliberately does NOT affect :func:`score`. They answer different questions — score is "does
    this match my skills", band is "is this the right level". A Director-of-Engineering-AI role
    can be a genuine 8/10 skills match and still be a rung too high; before this existed such
    titles tied with real team-lead roles at the top of the shortlist. Above-band roles are
    ranked lower, never dropped: title inflation varies wildly with company size.
    """
    above = _p(profile).pattern("above_band_titles")
    return "above_band" if above and above.search(job.get("title") or "") else "in_band"


def depth_risk_flag(job: dict, profile: Profile | None = None) -> bool:
    """True when the title names an area the candidate flagged as thinner on their CV.

    Configured per candidate as ``roles.depth_risk_titles`` (e.g. a backend lead who is weaker on
    data-platform work lists ``"data platform"``). An **advisory** only, same contract as
    :func:`level_band`: it never filters and never changes :func:`score`; the board surfaces it so
    interview prep can be weighted toward the depth a round may probe. An empty list means the
    candidate has no opinion — never "flag everything".
    """
    pat = _p(profile).pattern("depth_risk_titles")
    return bool(pat and pat.search(job.get("title") or ""))


def score(job: dict, profile: Profile | None = None) -> int:
    """1–10 match score from profile keywords plus target-band title floors."""
    prof = _p(profile)
    title = job.get("title") or ""
    text = (
        title
        + " "
        + (job.get("domain") or "")
        + " "
        + (job.get("snippet") or "")
        + " "
        + (job.get("message_body") or "")
    ).lower()

    keywords = prof.keywords
    boost = prof.boost_keywords
    hits = sum(1 for k in keywords if k in text)
    s = 1 + (hits // 2) + (2 if boost and any(k in text for k in boost) else 0)

    # Floor titles in the candidate's target band so keyword sparsity cannot bury them.
    targets = prof.pattern("target_titles")
    title_in_band = bool(targets.search(title)) if targets else False
    # A leadership-seeking profile also floors on any leadership title, not only the listed
    # ones — otherwise a real lead role phrased unusually falls through to keyword-only scoring.
    if not title_in_band and prof.require_leadership:
        title_in_band = bool(_LEADERSHIP_TITLE.search(title))
    title_has_ai = bool(_AI_TITLE.search(title))
    if title_in_band and title_has_ai:
        s = max(s, 8)  # target-band AI/ML title → never discard on keywords alone
    elif title_in_band:
        s = max(s, 5)  # any target-band title floors mid so it reaches LLM review

    return min(10, max(1, s))
