from src.jobcore.scoring import is_relevant, level_band, score


def test_llm_leadership_title_scores_high():
    assert score({"title": "LLM Team Lead", "message_body": "lead an LLM/agents team"}) >= 8


def test_llm_leadership_beats_generic():
    llm = score({"title": "LLM Team Lead"})
    generic = score({"title": "Office Coordinator", "message_body": "manage the office"})
    assert llm > generic


def test_is_relevant_backend_lead():
    assert is_relevant({"title": "Backend Team Lead", "domain": "Software"}) is True


def test_is_relevant_excludes_qa_ic():
    assert is_relevant({"title": "QA Automation Engineer", "domain": "QA"}) is False


# --- embedded / firmware domain mismatch (candidate has no embedded experience) --


def test_is_relevant_excludes_embedded_and_firmware_leadership():
    # Defense-prime titles that leaked through before — a real domain the candidate lacks.
    for title in (
        "Embedded Software Team Lead",
        "RT embedded & AI software team leader",
        "Software Embedded Manager",
        "Firmware Team Leader",
        "FPGA Design Team Lead",
        "RTOS Software Team Lead",
    ):
        assert is_relevant({"title": title, "domain": "Software"}) is False, title


def test_is_relevant_keeps_embedded_fintech_and_analytics():
    # "embedded finance/payments/analytics" are SaaS/fintech domains, NOT hardware —
    # must not be caught by the embedded filter.
    assert is_relevant({"title": "Embedded Finance Backend Team Lead", "domain": "Software"}) is True
    assert is_relevant({"title": "Embedded Analytics Engineering Manager", "domain": "Software"}) is True


def test_is_relevant_still_keeps_plain_backend_lead():
    assert is_relevant({"title": "Backend Team Lead", "domain": "Software"}) is True


# --- hardware / manufacturing / NPI domain mismatch ---------------------------


def test_is_relevant_excludes_hardware_manufacturing_npi():
    for title in (
        "Engineering Program Manager NPI",
        "NPI Product Expert Team Leader",
        "Manufacturing Engineering Team Lead",
        "Production Line Team Leader",
        "Electro-Optics Team Lead",
        "PCB Design Team Lead",
    ):
        assert is_relevant({"title": title, "domain": "Operations"}) is False, title


def test_is_relevant_keeps_software_production_systems_lead():
    # "Production Systems / production engineering" in the SRE/software sense must survive —
    # only manufacturing 'production line/floor' is excluded.
    assert is_relevant({"title": "Production Systems Team Lead", "domain": "Software"}) is True


# --- systems/hardware "integration" vs software "integrations" ----------------
# Confirmed via the real Elbit JDs (ids 4428589455 / 4428592478): bare
# "Integration Team Lead" at the defense primes = RF/radar/hardware systems
# integration (EE degree, microwave, RF, mechanical) — a domain mismatch. But
# software/data/API integration leadership DOES fit the candidate, so guard it.


def test_is_relevant_excludes_bare_systems_integration_lead():
    for title in (
        "Integration Team Lead",
        "System Integration Team Lead",
        "Integration & Test Team Leader",
    ):
        assert is_relevant({"title": title, "domain": "Systems"}) is False, title


def test_is_relevant_keeps_software_and_data_integration_lead():
    for title in (
        "Software Integration Team Lead",
        "Data Integration Team Lead",
        "API Integrations Team Lead",
        "Platform Integrations Engineering Manager",
    ):
        assert is_relevant({"title": title, "domain": "Software"}) is True, title


# --- level band (altitude, separate from skills-fit score) --------------------


def test_level_band_demotes_director_and_above_titles():
    assert level_band({"title": "Director of Engineering"}) == "above_band"
    assert level_band({"title": "Director of Quality Engineering"}) == "above_band"
    assert level_band({"title": "VP R&D"}) == "above_band"
    assert level_band({"title": "Head of Engineering"}) == "above_band"
    assert level_band({"title": "Chief Technology Officer"}) == "above_band"


def test_level_band_keeps_lead_and_ambiguous_senior_manager_in_band():
    assert level_band({"title": "Backend Team Lead"}) == "in_band"
    assert level_band({"title": "Engineering Manager"}) == "in_band"
    assert level_band({"title": "Staff Software Engineer"}) == "in_band"
    assert level_band({"title": "Principal Tech Lead"}) == "in_band"
    # 'Senior Manager' is deliberately in-band: too ambiguous across companies to demote.
    assert level_band({"title": "Senior Manager - Detection and Response"}) == "in_band"


def test_level_band_never_changes_match_score():
    """Band is a separate sort axis. Score keeps meaning 'how well does this match my skills'."""
    director = {"title": "Director of Engineering, Automation, Performance & AI Quality"}
    assert level_band(director) == "above_band"
    assert score(director) == 8, "banding must not touch the score"


def test_level_band_handles_missing_title():
    assert level_band({}) == "in_band"
