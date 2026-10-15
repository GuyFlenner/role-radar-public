"""Quality gates — the SINGLE source of truth for the pipeline's deterministic
CV/posting checks. Both the interactive skills (cv-aligner, job-flow-reviewer)
and any batch driver rely on these; do not re-implement them elsewhere.

- Step 0 / liveness   → classify_liveness(page_text)
- Step 0.5 / knockout → find_knockout_gaps(jd_text, cv_text)
- Step 4   / ATS coverage → keyword_coverage(cv_text, terms)
- Step 4.5 / anti-drift → find_repeated_clauses(cv_text)
"""

import re
from collections import Counter

from .profile import Profile
from .scoring import default_profile


def _prof(profile: Profile | None = None) -> Profile:
    """Resolve the candidate profile these gates judge against."""
    return profile if profile is not None else default_profile()


# --- Step 4.5: anti-drift (near-verbatim clause repeated across sections) ----


def _normalize_clause(text: str) -> str:
    """Lowercase, strip markdown/punctuation, collapse whitespace — for clause comparison."""
    text = re.sub(r"[*_`#>\-]", " ", text.lower())
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def find_repeated_clauses(cv_text: str, min_words: int = 6, threshold: int = 2) -> list[tuple[str, int]]:
    """
    Detect near-verbatim clauses of >= min_words that appear >= threshold times
    across the CV — the drift the old 4+-occurrence single-word check missed
    (e.g. a distinctive 6-word phrase pasted verbatim into Summary, a Key
    Achievement, and an Experience bullet).

    Uses a sliding window of min_words tokens over the whole document so a
    flagship clause repeated across sections is caught even when the surrounding
    sentence differs.
    """
    tokens = _normalize_clause(cv_text).split()
    if len(tokens) < min_words:
        return []
    counts: Counter = Counter()
    for i in range(len(tokens) - min_words + 1):
        window = " ".join(tokens[i : i + min_words])
        counts[window] += 1
    # Collapse overlapping windows: keep the longest distinct repeated clause,
    # not every shifted sub-window of it.
    repeated = {w: c for w, c in counts.items() if c >= threshold}
    result = []
    for window, count in sorted(repeated.items(), key=lambda x: -x[1]):
        if not any(window in longer and window != longer for longer in repeated):
            result.append((window, count))
    return result


def keyword_coverage(cv_text: str, terms: list[str]) -> dict:
    """Deterministic ATS keyword coverage — the one QA dimension that used to be
    eyeballed (and therefore drifted between passes).

    `terms` is the JD's must-have list (the model extracts it in cv-aligner
    Step 2). Matching uses the SAME normalization as the drift gate — lowercase,
    markdown/punctuation stripped, `-` collapsed to a space, word-boundary — so
    that: (a) "code review" matches "code-review" and "**code review**" instead
    of reading as missing, (b) "ml" never matches inside "html", and (c) the
    number is REPEATABLE: the QA loop, the post-de-drift re-check, and the
    flow-reviewer all compute the identical coverage rather than three guesses.

    Returns {"present": [...], "missing": [...], "pct": int}. A near-miss the
    model judges satisfied (JD "8+ years" vs CV "15+ years") will show as
    missing here — that is correct; the string genuinely isn't present, and the
    model reconciles it. Empty `terms` → 100% (nothing required)."""
    norm = _normalize_clause(cv_text)
    present: list[str] = []
    missing: list[str] = []
    for term in terms:
        nt = _normalize_clause(term)
        if not nt:
            continue
        pat = r"(?<![a-z0-9])" + re.escape(nt) + r"(?![a-z0-9])"
        (present if re.search(pat, norm) else missing).append(term)
    total = len(present) + len(missing)
    pct = round(100 * len(present) / total) if total else 100
    return {"present": present, "missing": missing, "pct": pct}


def find_cover_letter_drift(cover_text: str, cv_text: str, min_words: int = 8) -> list[tuple[str, int]]:
    """Flag clauses of >= min_words that the cover letter copies VERBATIM from the CV.

    A cover letter should complement the CV in a different register (motivation,
    narrative) — not paste résumé bullets. Returns (clause, 1) for each distinct
    copied clause (empty = complementary/clean). min_words defaults to 8 (short
    natural overlaps like "engineering leader with 15 years" are fine; an 8-word
    verbatim run is lifted text)."""
    cov_tokens = _normalize_clause(cover_text).split()
    cv_tokens = _normalize_clause(cv_text).split()
    if len(cov_tokens) < min_words or len(cv_tokens) < min_words:
        return []
    cv_windows = {" ".join(cv_tokens[i : i + min_words]) for i in range(len(cv_tokens) - min_words + 1)}
    hits, seen = [], set()
    for i in range(len(cov_tokens) - min_words + 1):
        w = " ".join(cov_tokens[i : i + min_words])
        if w in cv_windows and w not in seen:
            seen.add(w)
            hits.append(w)
    # collapse overlapping windows → keep the longest distinct copied clause
    hits = [w for w in hits if not any(w in o and w != o for o in hits)]
    return [(w, 1) for w in hits]


# --- Step 0.5: knockout requirements the candidate can't meet ----------------
# These two sets used to be module constants describing ONE person: Java was "fine because Apex",
# Go/Rust were knockouts. For a second candidate that is simply wrong — her true Go experience
# would have been reported as a knockout gap, and a Java year-gate she genuinely fails would have
# been waved through. Both now come from the profile.
#
# An empty set means "not configured", which the caller must surface as SKIPPED, never as "clean".


def _known_langs(profile: Profile | None = None) -> set[str]:
    """Languages the candidate has — a JD year-gate on one of these is NOT a knockout."""
    return {s.lower() for s in _prof(profile).facts.get("known_languages", [])}


def _knockout_langs(profile: Profile | None = None) -> set[str]:
    """Languages the candidate lacks — a hard "N+ years <X>" gate on one IS a knockout."""
    return {s.lower() for s in _prof(profile).facts.get("knockout_languages", [])}


def find_knockout_gaps(jd_text: str, cv_text: str, profile: Profile | None = None) -> list[str]:
    """
    Scan the JD's required section for hard, quantified must-haves the candidate
    cannot meet — a specific-language year-gate for a language absent from the
    CV, a mandatory security clearance, or a mandatory advanced degree. Returns
    human-readable gap strings (empty = no knockouts).
    """
    jd_lower = jd_text.lower()
    cv_lower = cv_text.lower()
    gaps: list[str] = []

    knockout_langs = _knockout_langs(profile)
    if not knockout_langs:
        # Returning [] here would read as "no knockouts found", which is a lie when we never
        # looked. A skipped gate is not a green gate.
        gaps.append(
            "SKIPPED[knockout-languages]: profile facts.knockout_languages is empty — "
            "language year-gates were NOT checked"
        )

    # 1. A specific-language year-gate for a language the CV lacks, scoped to a
    #    clause: a year-gate binds the language only when both appear in the same
    #    clause (split on . ; newline and bullet separators). Word-boundary
    #    matching so "java" does not match "javascript".
    year_re = re.compile(r"(\d{1,2})\s*\+?\s*years?")
    for clause in re.split(r"[.;\n•]", jd_lower):
        ym = year_re.search(clause)
        if not ym:
            continue
        for lang in knockout_langs:
            bound = r"(?<![a-z0-9])" + re.escape(lang) + r"(?![a-z0-9+])"
            if re.search(bound, cv_lower):
                continue
            if re.search(bound, clause):
                gaps.append(f"KNOCKOUT: JD requires {ym.group(1)}+ years {lang} — not in CV")

    # 2. Security clearance — flag when the JD requires one and the CV makes no
    #    affirmative clearance claim (a CV saying "no clearance" is NOT a claim).
    # Require an AFFIRMATIVE clearance requirement, and never fire on a "no
    # clearance" / "without clearance" note (our own JD analysis lines say that,
    # which used to false-flag JFrog/Mastercard as skip). Bare "clearance" and
    # bare Hebrew "סיווג" (= "classification", too broad) are intentionally NOT
    # matched — only "security clearance", "clearance required/needed", an
    # explicit "requires … clearance", or "סיווג ביטחוני".
    jd_wants_clearance = re.search(
        r"(?<!no )(?<!without )"
        r"(security clearance|clearance (is )?(required|needed)|"
        r"requires? (an? )?(security )?clearance|סיווג ביטחוני)",
        jd_lower,
    )
    cv_claims_clearance = re.search(r"(?<!no )(?<!without )(?<!lacks )\bclearance\b", cv_lower)
    if jd_wants_clearance and not cv_claims_clearance:
        gaps.append("KNOCKOUT: JD requires a security clearance — candidate has none")

    # 3. Mandatory advanced degree (only flag if CV shows no MSc/PhD).
    if re.search(r"(master'?s|m\.?sc|phd|ph\.?d)\b[^.\n]{0,40}(required|must)", jd_lower) and not re.search(
        r"m\.?sc|master|phd|ph\.?d", cv_lower
    ):
        gaps.append("KNOCKOUT: JD mandates an advanced degree (MSc/PhD) the candidate lacks")

    seen, out = set(), []
    for g in gaps:
        if g not in seen:
            seen.add(g)
            out.append(g)
    return out


# --- Step 4.5 / facts: claims that contradict the candidate's real history ---
# Encodes candidate ground truth so a whole CLASS of accuracy drift
# (wrong-employer tech attribution, banned/false terms, leaked internal
# annotations) is caught DETERMINISTICALLY before a CV renders — instead of
# reactively, by eye, one at a time. This closes the "honesty is the last
# eyeballed dimension" gap (same root cause as the ATS drift already fixed).
#
# This block is now split in two. `_GENERIC_CLAIM_PATTERNS` below is true for ANY candidate and
# stays in code. Everything that described one person's history — MongoDB, Elastic Beanstalk,
# an IDF rank, a specific coding-assistant preference — moved to `facts.never_claim` /
# `facts.rank_or_credential` in the profile, because for a second candidate those "false claims"
# may be plainly true, and the gate would have called her honest CV a lie.
#
# Each entry: (label, compiled pattern, reason). High precision by design — a
# noisy facts gate gets ignored. Context-sensitive rules (wrong-employer tech)
# are handled per-line below, since they depend on co-occurrence, not a bare term.
_GENERIC_CLAIM_PATTERNS = [
    (
        "zero-hallucinations",
        re.compile(r"zero\s+hallucinations?", re.I),
        "Overclaim — 'zero hallucinations' is not substantiable",
    ),
    (
        "internal-annotation",
        re.compile(
            r"\((?:differentiator|nice[-\s]?to[-\s]?have|bonus|advantage|standout|foot[-\s]?in[-\s]?door)\)", re.I
        ),
        "Internal tailoring annotation leaked into candidate-facing text",
    ),
    (
        "ecs-k8s-conflation",
        re.compile(r"kubernetes\s*\((?:k8s/)?eks/ecs\s*fargate\)", re.I),
        "ECS is not Kubernetes — 'Kubernetes (EKS/ECS Fargate)' conflates them",
    ),
]

# Credentials that must be stated exactly or not at all (rank inflation is a classic CV drift).
_CREDENTIAL_TOKENS = re.compile(r"\b(captain|major|colonel|sergeant|lieutenant|commander)\b", re.I)


def _never_claim_patterns(profile: Profile | None = None):
    """Per-candidate banned terms, compiled from `facts.never_claim`."""
    prof = _prof(profile)
    out = []
    for term in prof.facts.get("never_claim", []):
        out.append(
            (
                f"never-claim:{term}",
                re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", re.I),
                f"'{term}' is on this candidate's never-claim list",
            )
        )
    return out


def _credential_violations(cv_text: str, profile: Profile | None = None) -> list[str]:
    """A rank/credential other than the profile's exact one is drift.

    Returns SKIPPED rather than [] when the check cannot run but the CV *does* mention a
    credential. The outer `has_facts` guard only covers a wholly absent `facts` block; a profile
    with facts but no `rank_or_credential` slipped through it and reported clean — for a check
    whose entire job is catching an inflated rank on a CV a real recruiter will read.
    """
    prof = _prof(profile)
    want = (prof.facts.get("rank_or_credential") or "").strip()
    if not want:
        found = _CREDENTIAL_TOKENS.search(cv_text)
        if found and prof.has_facts:
            return [
                f"SKIPPED[credential]: CV states a credential ('{found.group(0)}') but the profile "
                f"sets no `facts.rank_or_credential` — it was NOT verified"
            ]
        return []
    out = []
    for m in _CREDENTIAL_TOKENS.finditer(cv_text):
        if m.group(0).lower() != want.lower():
            out.append(f"FACT[credential]: credential must read exactly '{want}', found '{m.group(0)}'")
    return out


def _employer_attribution_violations(cv_text: str, profile: Profile | None = None) -> list[str]:
    """Catch an achievement attributed to the wrong employer.

    The original version hardcoded one history: Kubernetes on a "ExampleSaaS" line was a lie because
    that platform ran on ECS Fargate, while the same word on an "ExampleStream" line was true. That is
    a genuinely valuable check, but it is per-candidate data. It now derives from
    `facts.employers[].markers`: if a line carries exactly one employer's markers and also carries
    a marker that belongs only to a different employer, that is cross-attribution.
    """
    employers = []
    for emp in _prof(profile).facts.get("employers", []):
        name = (emp.get("name") or "").strip()
        markers = {m.lower() for m in emp.get("markers", []) if m}
        if not name:
            continue
        # Attribution keys on the employer NAME (plus any aliases), never on the markers:
        # a cross-attributed line carries BOTH employers' markers by definition, so using
        # markers to decide whose line it is makes the very case we are hunting look ambiguous.
        names = {name.lower(), *(a.lower() for a in emp.get("aliases", []))}
        employers.append((name, names, markers))
    if len(employers) < 2:
        # Not "clean" — unrunnable. Cross-attribution needs at least two employers to compare,
        # so a one-employer profile got silent [] from the check most likely to catch the
        # career-defining lie (a marquee technology credited to the wrong job).
        if _prof(profile).has_facts:
            return [
                f"SKIPPED[employer-attribution]: profile lists {len(employers)} employer(s) with "
                f"markers; at least 2 are needed — cross-attribution was NOT checked"
            ]
        return []

    out = []
    for raw in cv_text.splitlines():
        line = raw.strip()
        low = line.lower()
        owners = [(n, m) for n, names, m in employers if any(a in low for a in names)]
        if len(owners) != 1:
            continue  # unattributed, or names two employers — not enough signal to judge
        this_name, this_markers = owners[0]
        for other_name, _names, other_markers in employers:
            if other_name == this_name:
                continue
            stolen = sorted(m for m in other_markers - this_markers if m in low)
            if stolen:
                out.append(
                    f"FACT[wrong-employer]: line attributed to {this_name} also claims "
                    f"{', '.join(stolen)} — those belong to {other_name}: '{line[:100]}'"
                )
    return out


def check_cv_facts(cv_text: str, profile: Profile | None = None) -> list[str]:
    """Scan a CV for claims that contradict the candidate's real history.

    Three sources: (a) generic overclaims true for anyone, (b) this candidate's
    `facts.never_claim` terms and exact credential, and (c) wrong-employer attribution derived
    from `facts.employers[].markers`.

    **With no `facts` configured, (b) and (c) cannot run.** An explicit SKIPPED line is returned
    instead of an empty list, because an empty list reads as "this CV is honest" — and a skipped
    gate is not a green gate.
    """
    prof = _prof(profile)
    violations: list[str] = []

    if not prof.has_facts:
        violations.append(
            "SKIPPED[cv-facts]: profile has no `facts` block — candidate-specific honesty checks "
            "did NOT run (generic overclaim patterns still applied)"
        )

    for label, pat, reason in _GENERIC_CLAIM_PATTERNS + _never_claim_patterns(prof):
        m = pat.search(cv_text)
        if m:
            violations.append(f"FACT[{label}]: {reason} (found: '{m.group(0).strip()}')")

    violations.extend(_credential_violations(cv_text, prof))
    violations.extend(_employer_attribution_violations(cv_text, prof))
    seen, out = set(), []
    for v in violations:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


# --- Over-qualification ADVISORY (never a filter) ----------------------------
# A candidate who far exceeds a role's stated band — leads a much larger org than
# the JD's small-team ask, or carries a senior-manager/director title against a
# hands-on "team lead" req — can read as over-level at recruiter screen (a common
# pre-screen auto-defer, e.g. the JFrog R&D-TL rejection 2026-07-14). This is an
# ADVISORY the skills surface HONESTLY. Per explicit user directive: it must NEVER
# filter, drop, deprioritize, or lower the score of a position — flag, don't gate.
_JD_SMALL_TEAM = re.compile(
    r"team of\s*(\d{1,2})(?:\s*(?:[-–]|to)\s*(\d{1,2}))?\s*(?:developers?|engineers?|devs?)", re.I
)
_JD_LOW_LEAD_YEARS = re.compile(
    r"(\d{1,2})\s*\+?\s*(?:[-–]\s*\d{1,2}\s*)?years?[^.\n]{0,30}?(?:lead|leading|managing|management|as a leader)",
    re.I,
)
_CV_BIG_TEAM = re.compile(r"(\d{2,3})(?:\s*(?:[-–]|to)\s*(\d{2,3}))?\s*(?:engineers?|developers?|devs?|people)", re.I)
_CV_SENIOR_TITLE = re.compile(
    r"\b(engineering manager|director of|vice president|\bvp\b|head of|group manager)\b", re.I
)


def overqualification_note(jd_text: str, cv_text: str) -> str | None:
    """ADVISORY (never a filter): warn when the candidate reads as over-level for
    the role's band.

    Fires only when a LOW JD band (a small team and/or a low leadership-years bar)
    co-occurs with a HIGH candidate signal (a large team span, or a
    senior-manager/director title). Returns one human-readable note, or None.

    Callers MUST surface this and MUST NOT drop, deprioritize, or rescore the
    position on it — the candidate decides. It exists so an honest "you may read
    as over-level here; frame toward hands-on depth" reaches the user, not so the
    pipeline hides the role."""
    jd, cv = jd_text or "", cv_text or ""

    jd_signals: list[str] = []
    m = _JD_SMALL_TEAM.search(jd)
    if m and int(m.group(1)) <= 6:
        span = f"{m.group(1)}-{m.group(2)}" if m.group(2) else m.group(1)
        jd_signals.append(f"team of {span} devs")
    m = _JD_LOW_LEAD_YEARS.search(jd)
    if m and int(m.group(1)) <= 3:
        jd_signals.append(f"~{m.group(1)}+ yr leadership bar")

    cv_signals: list[str] = []
    best = 0
    for mm in _CV_BIG_TEAM.finditer(cv):
        best = max(best, int(mm.group(2) or mm.group(1)))
    if best >= 10:
        cv_signals.append(f"led ~{best} engineers")
    tm = _CV_SENIOR_TITLE.search(cv)
    if tm:
        cv_signals.append(f"title '{tm.group(1).strip()}'")

    if jd_signals and cv_signals:
        return (
            "OVER-QUALIFICATION (advisory — do NOT filter/deprioritize/rescore): "
            f"JD band is low ({'; '.join(jd_signals)}) but the CV signals senior scope "
            f"({'; '.join(cv_signals)}). May read as over-level at recruiter screen — consider "
            "framing toward hands-on depth over org span. Surface honestly; keep the position."
        )
    return None


# --- Step 0 / P3: posting liveness -------------------------------------------
_CLOSED_SIGNALS = re.compile(
    r"no longer accepting|no longer available|position (is )?closed|"
    r"position (has been )?filled|currently no open positions|"
    r"no open positions|posting has expired|not accepting applications",
    re.I,
)
_OPEN_SIGNALS = re.compile(
    r"apply now|submit (your )?(resume|cv|application)|application form|"
    r"upload (your )?(cv|resume)|apply for this",
    re.I,
)


def classify_liveness(page_text: str | None) -> str:
    """Return 'OPEN' | 'CLOSED' | 'UNVERIFIED' from fetched apply-page text.

    JS-rendered/SPA shells (Comeet, Workday, Amdocs) return template markers or
    empty bodies → UNVERIFIED, never a false OPEN/CLOSED. Tailor only on OPEN;
    treat UNVERIFIED as 'confirm in-browser before applying'."""
    if not page_text or not page_text.strip():
        return "UNVERIFIED"
    if "{{" in page_text or "loading" in page_text.lower()[:200]:
        return "UNVERIFIED"
    if _CLOSED_SIGNALS.search(page_text):
        return "CLOSED"
    if _OPEN_SIGNALS.search(page_text):
        return "OPEN"
    return "UNVERIFIED"


# --- Salary benchmark: a market-grounded expectation range must exist ---------
#
# Root cause this closes: salary was a free-text "[fill from your expectations]"
# placeholder in interview-prep, so a run could ship with no benchmarked range —
# or with an IC-developer number anchored to the wrong band. This gate makes the
# presence of a *sourced* range checkable the same way ATS coverage is.

# Market-agnostic salary sources. A candidate's local boards come from `salary.sources` in the
# profile and are added to these — the original list was Israel-specific, which meant a Berlin
# search could never satisfy the "cite >= 2 sources" rule no matter what it cited.
SALARY_SOURCES = (
    "glassdoor",
    "levels.fyi",
    "levels",
    "payscale",
    "ravio",
    "linkedin salary",
    "indeed",
    "stackoverflow survey",
)


def _salary_sources(profile: Profile | None = None) -> tuple[str, ...]:
    extra = tuple(s.lower() for s in _prof(profile).salary.get("sources", []))
    return SALARY_SOURCES + extra


# A money figure in any currency: "58,000" / "58K" / "₪62k" / "€95,000" / "52-68K".
_MONEY_FIGURE = re.compile(r"(?:[₪$€£]\s*)?\d{2,3}(?:,\d{3})?\s*k?\b", re.I)
_RANGE_HINT = re.compile(r"\b(range|target|walk[- ]?away|floor|open with|expect)\b", re.I)


def check_salary_benchmark(text: str, profile: Profile | None = None) -> dict:
    """Validate that a position carries a market-grounded salary expectation.

    Pass criteria (all required):
      - at least TWO money figures (a range, not a single point),
      - at least one range/target/walk-away keyword (it's an expectation, not a
        stray number in prose),
      - at least TWO distinct named sources (so the number is benchmarked, not invented),
        drawn from the market-agnostic list plus the profile's own `salary.sources`.

    `text` is the concatenated salary section / SALARY_BENCHMARK.md content.
    Returns {ok, figures, sources, reasons}. Mirrors keyword_coverage's shape so
    job-flow-reviewer can print it inline.
    """
    figures = _MONEY_FIGURE.findall(text or "")
    sources = sorted({s for s in _salary_sources(profile) if s in (text or "").lower()})
    reasons = []
    if len(figures) < 2:
        reasons.append("needs a range (>=2 money figures), not a single point")
    if not _RANGE_HINT.search(text or ""):
        reasons.append("no range/target/walk-away framing found")
    if len(sources) < 2:
        reasons.append(f"needs >=2 named sources (found {len(sources)}: {sources or 'none'})")
    return {
        "ok": not reasons,
        "figures": len(figures),
        "sources": sources,
        "reasons": reasons,
    }


# --- CV portability: a résumé must not carry cover-letter "pitch" language ------
# Root cause of a real miss (Centerity/Cato/NICE CVs, 2026-07-19): tailoring drifted
# into embedding the TARGET company's name/strategy and "this role/this team" framing
# INSIDE the CV body — a cover-letter move that reads try-hard and makes the CV
# non-portable. The drift/facts/coverage gates don't see it (it's tone/portability,
# and ATS-coverage actively incentivises it). This gate makes it deterministic.
_CV_PITCH_PATTERNS = [
    r"\ba direct match for\b",
    r"\bthis (role|team|position|company)\b",
    r"\bthe role (is|targets|requires|needs|calls for|is chartered|demands)\b",
    r"\bexactly what (this|the) (role|team|company|position)\b",
    r"\bat the (exact )?intersection (this|the)\b",
    r"\b(is|are|role is) chartered to\b",
    r"\bwhat (this|the) (role|team|company) (secures|solves|targets|needs|is)\b",
]
_CV_PITCH_RES = [re.compile(p, re.I) for p in _CV_PITCH_PATTERNS]


def find_company_pitch_in_cv(cv_text: str, company: str | None = None) -> list[str]:
    """A CV is a portable candidate document, NOT a cover letter. Flag cover-letter
    'pitch' language embedded in the CV body: the TARGET company's name (the candidate
    doesn't work there yet) and constructions that address 'this role / the role / this
    team' or the company's strategy ('a direct match for X's push toward Y', 'the same
    problem X solves'). Those belong in the cover letter, never the résumé. Returns
    human-readable hit strings (empty = clean). `company` is the target-company name
    (from the JD / position slug) — when given, its appearance in the CV is flagged.
    """
    hits: list[str] = []
    if company:
        names = {company.strip()}
        first = company.strip().split()[0] if company.strip() else ""
        if len(first) >= 4:
            names.add(first)
        for name in names:
            if len(name) >= 4 and re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", cv_text):
                hits.append(f"target company '{name}' named in the CV body — a CV is not a cover letter")
                break
    for rx in _CV_PITCH_RES:
        for m in rx.finditer(cv_text):
            hits.append(f"cover-letter pitch phrase in CV: '{m.group(0).strip()}'")
    seen, out = set(), []
    for h in hits:
        k = h.lower()
        if k not in seen:
            seen.add(k)
            out.append(h)
    return out


# --- Private-scope firewall ---------------------------------------------------
# Some facts about a job search are true and must still never reach a recruiter: a pending legal
# or HR process, a former manager's name, an exact salary figure, the dates of a termination. They
# end up in drafts because the drafting agent has read the same notes the candidate wrote. This is
# the deterministic backstop: the CANDIDATE declares the terms and dates in ``profile.privacy``,
# and any CV / cover letter / outreach text is scanned for them.
#
# With nothing configured this returns [] — "no firewall configured", surfaced by
# ``Profile.brief()`` — because a candidate with nothing private is a legitimate state and failing
# every CV for it would train people to ignore the gate.

_MONTH_NAMES = "january february march april may june july august september october november december".split()


def _private_date_patterns(iso_dates: list[str]) -> list[str]:
    """Every form a person is likely to type for each date: ISO, D.M.Y / D/M/Y, "July 15(th)", "15(th) July"."""
    pats: list[str] = []
    for iso in iso_dates:
        try:
            y, m, d = (int(x) for x in iso.split("-"))
        except ValueError:
            continue
        mname = _MONTH_NAMES[m - 1] if 1 <= m <= 12 else None
        pats.append(rf"\b{y:04d}-{m:02d}-{d:02d}\b")
        pats.append(rf"\b0?{d}[./]0?{m}[./]{y}\b")
        if mname:
            pats.append(rf"\b{mname}\s+0?{d}(?:st|nd|rd|th)?\b")
            pats.append(rf"\b0?{d}(?:st|nd|rd|th)?\s+{mname}\b")
    return pats


def find_private_scope_leak(text: str, profile: Profile | None = None) -> list[str]:
    """Flag any of the candidate's declared private terms or dates in candidate-facing text.

    Terms match on word boundaries, case-insensitively; a hit means a human re-reads that line.
    Names may false-positive on an unrelated word — that is a deliberate trade: a wrongly flagged
    line costs a second of reading, a missed one can cost the whole search. Returns de-duplicated,
    human-readable hit strings; the text of the hit is shown so the line can be found.
    """
    prof = _prof(profile)
    pats = [rf"\b{re.escape(t)}\b" for t in prof.private_terms] + _private_date_patterns(prof.private_dates)
    hits: list[str] = []
    for p in pats:
        for m in re.finditer(p, text or "", re.I):
            hits.append(f"private-scope leak in candidate-facing text: '{m.group(0).strip()}'")
    seen: set[str] = set()
    out: list[str] = []
    for h in hits:
        k = h.lower()
        if k not in seen:
            seen.add(k)
            out.append(h)
    return out
