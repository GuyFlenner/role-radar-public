"""Quality-gate tests (re-homed from src/agents/tests). Gates are now the single
source of truth in src/jobcore/gates.py.

Run: uv run pytest src/jobcore/tests -q
"""

import re

from src.jobcore.gates import (
    check_cv_facts,
    check_salary_benchmark,
    classify_liveness,
    find_cover_letter_drift,
    find_knockout_gaps,
    find_repeated_clauses,
    keyword_coverage,
    overqualification_note,
)

# --- Over-qualification advisory (NON-BLOCKING) ------------------------------

_JFROG_JD = (
    "Lead a team of 2-4 developers. At least 1-2 years of leading experience and at least 10 years as an engineer."
)
_SENIOR_CV = "Led teams of 12-20 engineers as Engineering Manager; 15+ years, 5+ leading."


def test_overqual_fires_on_band_mismatch():
    note = overqualification_note(_JFROG_JD, _SENIOR_CV)
    assert note is not None
    assert "advisory" in note.lower() and "do NOT filter" in note


def test_overqual_silent_when_band_matches():
    # A JD asking for a large org / high bar → no over-qualification signal.
    jd = "Lead a team of 15-20 developers. At least 8 years of leading experience."
    assert overqualification_note(jd, _SENIOR_CV) is None


def test_overqual_silent_without_senior_cv_signal():
    junior_cv = "Team lead of a small squad; 6 years experience, hands-on."
    assert overqualification_note(_JFROG_JD, junior_cv) is None


def test_overqual_returns_note_not_a_filter_action():
    # Contract: it returns a string/None — it never mutates or drops anything.
    assert isinstance(overqualification_note(_JFROG_JD, _SENIOR_CV), str)
    assert overqualification_note("", "") is None


# --- ATS keyword coverage (deterministic, repeatable) ------------------------


def test_coverage_counts_present_and_missing():
    cv = "Led scalable distributed systems in Python with strong code-review standards."
    cov = keyword_coverage(cv, ["python", "scalable", "kubernetes"])
    assert set(cov["present"]) == {"python", "scalable"}
    assert cov["missing"] == ["kubernetes"]
    assert cov["pct"] == 67


def test_coverage_matches_hyphen_and_markdown_variants():
    # "code review" (JD term, spaced) must match "code-review" (CV, hyphenated)
    # and markdown emphasis must not block a match — this is the false-negative
    # that made a covered term look missing at score time.
    cv = "- **Pragmatic code-review** standards across the team."
    cov = keyword_coverage(cv, ["code review"])
    assert cov["missing"] == []


def test_coverage_word_boundary_no_substring_false_hit():
    # "ml" must not match inside "html"; a real gap must be reported as missing.
    cv = "Built HTML dashboards and LLM pipelines."
    cov = keyword_coverage(cv, ["ml"])
    assert cov["missing"] == ["ml"]


def test_coverage_empty_terms_is_full():
    assert keyword_coverage("anything", [])["pct"] == 100


# --- cover-letter drift (clauses copied verbatim from the CV) -----------------


def test_cover_letter_copy_flagged():
    cv = "I architected and shipped a production multi-tenant AI agent platform on AWS Bedrock."
    cover = "In my last role I architected and shipped a production multi-tenant AI agent platform on AWS Bedrock, and I loved it."
    hits = find_cover_letter_drift(cover, cv, min_words=8)
    assert hits, "should flag the 8+ word clause copied verbatim from the CV"


def test_cover_letter_complementary_ok():
    cv = "Architected a production AI agent platform (multi-MCP orchestration, RAG, agents)."
    cover = "What draws me to your team is the chance to make market-scale AI both fast and trustworthy in production."
    assert find_cover_letter_drift(cover, cv, min_words=8) == []


# --- Step 4.5: anti-drift (near-verbatim clause repeated across sections) ----


def test_repeated_clause_detected_across_sections():
    cv = (
        "## SUMMARY\nI built agentic systems integrating LLMs, business logic, and infrastructure daily.\n"
        "## ACHIEVEMENTS\n- Shipped agentic systems integrating LLMs, business logic, and infrastructure.\n"
        "## EXPERIENCE\n- Led agentic systems integrating LLMs, business logic, and infrastructure at scale.\n"
    )
    hits = find_repeated_clauses(cv, min_words=6, threshold=2)
    assert any("agentic systems integrating llms" in c for c, _ in hits), hits


def test_no_false_positive_on_distinct_sentences():
    cv = (
        "Led a team of twenty engineers across backend and data infrastructure.\n"
        "Reduced cloud spend by thirty percent through right-sizing and reserved instances.\n"
        "Built a real-time streaming platform on Kafka processing millions of events.\n"
    )
    assert find_repeated_clauses(cv, min_words=6, threshold=2) == []


# --- Step 0.5: knockout requirements ----------------------------------------


def test_knockout_language_years_gap():
    # Go is a genuine gap (not Apex/Java-family, candidate lacks it).
    jd = "Required: At least 8 years of hands-on development experience in Go."
    cv = "Python (primary), C#/.NET, JavaScript, SQL. 15+ years experience."
    gaps = find_knockout_gaps(jd, cv)
    assert any(re.search(r"\bgo\b", g.lower()) for g in gaps), gaps


def test_java_not_knockout_apex_family():
    jd = "Required: 8+ years hands-on Java."
    cv = "Python (primary), Apex/Salesforce, C#/.NET, JavaScript. 15+ years."
    assert find_knockout_gaps(jd, cv) == []


def test_javascript_vue_never_knockout():
    jd = "Required: 5+ years JavaScript; 3+ years Vue.js."
    cv = "React.js, JavaScript; managed Vue.js developers. 15+ years."
    assert find_knockout_gaps(jd, cv) == []


def test_no_knockout_when_language_present():
    jd = "Required: 8+ years hands-on Python."
    cv = "Python (primary), FastAPI, 15+ years."
    assert find_knockout_gaps(jd, cv) == []


def test_slash_list_alternatives_not_a_knockout():
    # 2026-07-09 regression: a research pass misread JFrog req 7899047's
    # "Proven experience in Python/Go/Java" as "all three required" and wrongly
    # flagged Go a knockout. Slash-lists are ALTERNATIVES (any one); with no
    # year-gate on Go specifically, and Python present, this is NOT a knockout.
    # The deterministic gate was right all along — lock that in.
    jd = "Proven experience in Python/Go/Java. Experience in scalable, fault-tolerant systems."
    cv = "Python (primary), C#/.NET and Apex (Java-family). 15+ years."
    assert find_knockout_gaps(jd, cv) == []


def test_ai_frameworks_named_requirement_not_a_language_knockout():
    # 2026-07-09: req 8045585 names no required language, only AI frameworks
    # (Cursor/Copilot). Must not manufacture a language knockout.
    jd = (
        "Experience working with AI models and frameworks (e.g., Cursor, GitHub Copilot). "
        "Hands-on experience in delivering complex features."
    )
    cv = "Python (primary); advanced Cursor and Claude Code. 15+ years."
    assert find_knockout_gaps(jd, cv) == []


# --- facts gate (claims contradicting the candidate's real history) ----------
# Encodes the 2026-07 drift class caught reactively (K8s@ExampleSaaS, Elastic
# Beanstalk, MongoDB, Codex, "(differentiator)") so it's caught deterministically.


def test_facts_k8s_at_examplestream_is_fine():
    # Kubernetes at ExampleStream is TRUE — must NOT be flagged.
    cv = "Led re-architecture of a Ruby pipeline into event-driven Python microservices on Kafka (AWS MSK) + Kubernetes — hundreds of millions of transactions/day."
    assert check_cv_facts(cv) == []


def test_facts_ecs_correct_examplesaas_is_clean():
    cv = "Shipped a multi-tenant SaaS platform (Python/FastAPI, ~47K LOC, 800+ tests) on AWS ECS Fargate (microservices)."
    assert check_cv_facts(cv) == []


def test_facts_internal_annotation_flagged():
    assert check_cv_facts("- **AI (differentiator):** production platform")
    assert check_cv_facts("- **Security (nice-to-have):** guardrails")


def test_facts_idf_captain_flagged():
    assert check_cv_facts("Israel Defense Forces — Captain, CRM systems.")


def test_knockout_security_clearance():
    jd = "Must hold an active security clearance (סיווג ביטחוני)."
    cv = "Engineering leader building AI platforms."
    gaps = find_knockout_gaps(jd, cv)
    assert any("clearance" in g.lower() for g in gaps), gaps


def test_no_clearance_annotation_is_not_a_knockout():
    # A JD/analysis note that says the role needs NO clearance must not trip the
    # clearance gate just because the word "clearance" appears — this false
    # positive wrongly flagged JFrog/Mastercard as skip.
    jd = "Knockout: none (Python satisfies language; no clearance/degree gate)."
    cv = "Engineering leader building AI platforms."
    assert find_knockout_gaps(jd, cv) == []


# --- Step 0 / P3: liveness classification ------------------------------------


def test_liveness_closed_signal():
    assert classify_liveness("We're sorry, but there are currently no open positions") == "CLOSED"
    assert classify_liveness("This position is no longer accepting applications.") == "CLOSED"


def test_liveness_open_signal():
    assert classify_liveness("Apply now — submit your resume and cover letter") == "OPEN"


def test_liveness_unverified_on_empty_or_spa():
    assert classify_liveness("") == "UNVERIFIED"
    assert classify_liveness("{{position.name}} loading…") == "UNVERIFIED"


# --- Salary benchmark (a sourced expectation range must exist) ----------------


def test_salary_benchmark_passes_with_range_framing_and_two_sources():
    text = (
        "Open/state range: 58,000-66,000 gross/month base. Target ~62K, "
        "walk-away ~54K. Sources: GotFriends, Levels.fyi, theWorker."
    )
    r = check_salary_benchmark(text)
    assert r["ok"] is True
    assert r["figures"] >= 2
    assert len(r["sources"]) >= 2


def test_salary_benchmark_fails_single_point_no_sources():
    r = check_salary_benchmark("We expect around 26K.")
    assert r["ok"] is False
    # single figure + no range framing + <2 sources
    assert any("range" in reason for reason in r["reasons"])
    assert any("sources" in reason for reason in r["reasons"])


def test_salary_benchmark_fails_range_but_no_sources():
    r = check_salary_benchmark("Target range 58K-66K, walk-away 54K.")
    assert r["ok"] is False
    assert r["figures"] >= 2
    assert any("sources" in reason for reason in r["reasons"])


# --- find_company_pitch_in_cv: CV must not carry cover-letter pitch language ----


def test_company_pitch_flags_target_name_and_this_role():
    from src.jobcore.gates import find_company_pitch_in_cv

    cv = (
        "Built a platform — the same governance problem Cato solves for coding agents. "
        "Building at the exact intersection this role targets."
    )
    hits = find_company_pitch_in_cv(cv, "Cato")
    joined = " ".join(hits).lower()
    assert hits
    assert "cato" in joined  # target company named in the body
    assert "this role" in joined


def test_company_pitch_flags_the_role_is_chartered():
    from src.jobcore.gates import find_company_pitch_in_cv

    cv = "This is the AI-era quality practice the role is chartered to scale across an org."
    assert find_company_pitch_in_cv(cv, "NICE")  # 'the role is chartered' caught even w/o the name


def test_company_pitch_clean_cv_returns_none():
    from src.jobcore.gates import find_company_pitch_in_cv

    cv = (
        "Architected and shipped a production multi-tenant AI agent platform "
        "(multi-agent orchestration with MCP tools); led ~20 engineers; cut cloud spend 30%."
    )
    assert find_company_pitch_in_cv(cv, "Centerity") == []


# --- facts gate: now profile-driven, tested against SYNTHETIC candidates -------
# These replace five tests that asserted one real person's history (ExampleSaaS/ECS,
# ExampleMedia/Rails, an IDF rank). Those "false claims" are plainly true for somebody else,
# which is exactly why the data moved into the profile.


def test_never_claim_term_is_flagged(backend_profile):
    v = check_cv_facts("Built the ingestion layer on MongoDB and Kafka.", backend_profile)
    assert any("never-claim:mongodb" in x for x in v), v


def test_the_same_term_is_fine_for_a_candidate_who_really_has_it(frontend_profile):
    """The whole point: one candidate's overclaim is another's true skill."""
    v = check_cv_facts("Built the ingestion layer on MongoDB.", frontend_profile)
    assert not any("mongodb" in x.lower() for x in v), v


def test_wrong_employer_attribution_is_flagged(backend_profile):
    """A line attributed to one employer must not claim another's signature work."""
    cv = "ExampleSaaS — multi-tenant platform processing hundreds of millions of events on MSK."
    v = check_cv_facts(cv, backend_profile)
    assert any("wrong-employer" in x for x in v), v


def test_correctly_attributed_line_is_not_flagged(backend_profile):
    cv = "ExampleStream — Kafka/MSK pipeline at hundreds of millions of events per day."
    v = check_cv_facts(cv, backend_profile)
    assert not any("wrong-employer" in x for x in v), v


def test_wrong_credential_is_flagged(backend_profile):
    v = check_cv_facts("Served as a Captain in the reserves.", backend_profile)
    assert any("credential" in x for x in v), v
    assert not check_cv_facts("Served as a Major in the reserves.", backend_profile)


def test_generic_overclaim_applies_to_every_candidate(every_profile):
    v = check_cv_facts("Delivered a RAG system with zero hallucinations.", every_profile)
    assert any("zero-hallucinations" in x for x in v), v


def test_facts_gate_SKIPS_loudly_when_profile_has_no_facts(empty_profile):
    """A gate that never ran must never look like a gate that passed."""
    v = check_cv_facts("Anything at all.", empty_profile)
    assert any(x.startswith("SKIPPED[cv-facts]") for x in v), v


def test_knockout_gate_SKIPS_loudly_when_no_knockout_languages(empty_profile):
    gaps = find_knockout_gaps("Requires 5+ years Go.", "Python and SQL.", empty_profile)
    assert any(x.startswith("SKIPPED[knockout-languages]") for x in gaps), gaps


def test_knockout_is_candidate_specific(backend_profile, frontend_profile):
    """Go is a knockout for the backend fixture and also for the frontend one; Java differs."""
    jd = "You will need 5+ years of Java experience."
    cv = "Python, SQL, TypeScript."
    assert not any("java" in g.lower() for g in find_knockout_gaps(jd, cv, backend_profile))
    assert any("java" in g.lower() for g in find_knockout_gaps(jd, cv, frontend_profile))
