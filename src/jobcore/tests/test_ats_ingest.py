"""ATS ingest + shortlist tests — offline (fetch and liveness injected)."""

import json
from datetime import date, datetime, timedelta

from src.jobcore import ats_ingest, ats_shortlist
from src.jobcore.ats_ingest import load_seen, save_seen


def _recent_iso():
    return (date.today() - timedelta(days=5)).isoformat()


def _stale_iso():
    return (datetime.now().date() - timedelta(days=200)).isoformat()


def _gh_jobs(*titles_dates):
    return {
        "jobs": [
            {
                "title": t,
                "location": {"name": "Netanya, Israel"},
                "absolute_url": f"https://boards.greenhouse.io/acme/jobs/{i}",
                "updated_at": d,
                "content": "<p>Python backend microservices AI agent.</p>",
            }
            for i, (t, d) in enumerate(titles_dates)
        ]
    }


def _write_config(tmp_path, targets):
    p = tmp_path / "ats_targets.json"
    p.write_text(json.dumps({"targets": targets}), encoding="utf-8")
    return p


# --- config loading ----------------------------------------------------------


def test_load_targets_validates_and_skips_bad(tmp_path):
    cfg = _write_config(
        tmp_path,
        [
            {"company": "Acme", "ats": "greenhouse", "token": "acme"},
            {"company": "NoToken", "ats": "greenhouse"},  # missing token → skip
            {"company": "Weird", "ats": "workday", "token": "x"},  # unknown ats → skip
            {"company": "Comeety", "ats": "comeet", "uid": "AA", "token": "t"},
        ],
    )
    targets = ats_ingest.load_targets(cfg)
    assert [t["company"] for t in targets] == ["Acme", "Comeety"]


def test_load_targets_missing_file(tmp_path):
    assert ats_ingest.load_targets(tmp_path / "nope.json") == []


# --- fetch + score integration ----------------------------------------------


def test_build_positions_scores_and_ranks(tmp_path):
    targets = [{"company": "Acme", "ats": "greenhouse", "token": "acme"}]
    payload = _gh_jobs(("Software Team Lead", _recent_iso()), ("AI Team Lead", _recent_iso()))
    jobs, stale = ats_ingest.build_positions(
        targets,
        delta_only=False,
        get_json=lambda url: payload,
    )
    assert len(jobs) == 2
    assert jobs[0]["match_score"] >= 8  # AI-leadership floored high, ranked first
    assert all(j["source"].startswith("greenhouse:") for j in jobs)


def _gh_jobs_at(*title_loc_pairs):
    """Greenhouse payload with explicit per-role locations (recent dates)."""
    return {
        "jobs": [
            {
                "title": t,
                "location": {"name": loc},
                "absolute_url": f"https://boards.greenhouse.io/acme/jobs/{i}",
                "updated_at": _recent_iso(),
                "content": "<p>Python backend AI agent.</p>",
            }
            for i, (t, loc) in enumerate(title_loc_pairs)
        ]
    }


def test_geo_gate_drops_non_israel_by_default(tmp_path):
    targets = [{"company": "NICE", "ats": "greenhouse", "token": "nice"}]
    payload = _gh_jobs_at(
        ("AI Team Lead", "Australia - Sydney"),
        ("Software Team Lead", "Netanya, Israel"),
        ("Engineering Manager", "USA - Sandy, UT"),
        ("Backend Team Lead", "Israel, Herzliya"),
    )
    # default: commute-band gate ON
    jobs, _ = ats_ingest.build_positions(targets, delta_only=False, get_json=lambda url: payload)
    locs = {j["location"] for j in jobs}
    assert locs == {"Netanya, Israel", "Israel, Herzliya"}
    assert all(j["in_preferred_geo"] == "Yes" for j in jobs)


def test_geo_gate_disabled_keeps_everything(tmp_path):
    targets = [{"company": "NICE", "ats": "greenhouse", "token": "nice"}]
    payload = _gh_jobs_at(
        ("AI Team Lead", "Australia - Sydney"),
        ("Software Team Lead", "Netanya, Israel"),
    )
    jobs, _ = ats_ingest.build_positions(
        targets,
        delta_only=False,
        require_preferred_location=False,
        get_json=lambda url: payload,
    )
    assert len(jobs) == 2  # Sydney kept when gate is off (--anywhere)


def test_csv_path_unaffected_by_geo_gate():
    # The shared core defaults require_preferred_location=False → CSV behavior unchanged.
    raw = [
        {
            "title": "Backend Team Lead",
            "company": "AbroadCo",
            "location": "Berlin, Germany",
            "domain": "Software",
            "url": "u",
            "snippet": "python backend",
            "posted_date": None,
        },
    ]
    from src.jobcore.csv_ingest import filter_score_positions

    jobs, _ = filter_score_positions(raw, delta_only=False)
    assert len(jobs) == 1 and jobs[0]["in_preferred_geo"] == ""


def test_commute_extra_widens_geo_for_that_target_only(tmp_path):
    # A non-defense greenhouse target's Haifa role is dropped by the central gate...
    gh_targets = [{"company": "NICE", "ats": "greenhouse", "token": "nice"}]
    gh_payload = _gh_jobs_at(("AI Team Lead", "Haifa, Israel"))
    gh_jobs, _ = ats_ingest.build_positions(gh_targets, delta_only=False, get_json=lambda url: gh_payload)
    assert gh_jobs == []

    # ...but a defense target carrying commute_extra=["haifa"] keeps its Haifa role.
    _HTML = (
        '<div data-entity-urn="urn:li:jobPosting:4399001">'
        '<a class="base-card__full-link" href="https://linkedin.com/jobs/view/x-4399001"></a>'
        '<h3 class="base-search-card__title">AI Team Lead</h3>'
        '<span class="job-search-card__location">Haifa, Israel</span></div>'
    )
    li_targets = [{"company": "Elbit Systems", "ats": "linkedin", "company_id": "1", "commute_extra": ["haifa"]}]
    li_jobs, _ = ats_ingest.build_positions(li_targets, delta_only=False, get_text=lambda url: _HTML)
    assert len(li_jobs) == 1
    assert li_jobs[0]["location"] == "Haifa, Israel"
    assert li_jobs[0]["source"] == "linkedin:Elbit Systems"
    # Haifa isn't part of the central band, so in_preferred_geo stays blank (it's an extra).
    assert li_jobs[0]["in_preferred_geo"] == ""


def test_build_positions_drops_stale(tmp_path):
    targets = [{"company": "Acme", "ats": "greenhouse", "token": "acme"}]
    payload = _gh_jobs(("Backend Team Lead", _recent_iso()), ("Engineering Manager", _stale_iso()))
    jobs, stale = ats_ingest.build_positions(targets, delta_only=False, get_json=lambda url: payload)
    titles = {j["title"] for j in jobs}
    assert "Backend Team Lead" in titles and "Engineering Manager" not in titles
    assert stale == 1


def test_one_dead_board_does_not_abort(tmp_path):
    targets = [
        {"company": "Dead", "ats": "greenhouse", "token": "dead"},
        {"company": "Live", "ats": "greenhouse", "token": "live"},
    ]

    def flaky_get(url):
        return None if "dead" in url else _gh_jobs(("Backend Team Lead", _recent_iso()))

    jobs, _ = ats_ingest.build_positions(targets, delta_only=False, get_json=flaky_get)
    assert len(jobs) == 1 and jobs[0]["company"] == "Live"


# --- delta seen-store --------------------------------------------------------


def test_seen_store_roundtrip_tc_and_id(tmp_path):
    store = tmp_path / ".last_ats_seen.json"
    ats_ingest.save_seen(
        store,
        [
            {"title": "Backend Team Lead", "company": "Acme"},  # no id → ("tc", ...)
            {"title": "AI Lead", "company": "Globex", "job_id": "7935939"},  # id → ("id", ...)
        ],
    )
    assert ats_ingest.load_seen(store) == {
        ("tc", "backend team lead", "acme"),
        ("id", "7935939"),
    }


def test_seen_store_reads_legacy_two_tuple(tmp_path):
    # A pre-upgrade store (list of [title, company]) still de-dupes — no reset.
    store = tmp_path / ".last_ats_seen.json"
    store.write_text('[["Backend Team Lead", "Acme"]]', encoding="utf-8")
    assert ats_ingest.load_seen(store) == {("tc", "backend team lead", "acme")}


def test_delta_filters_seen_by_job_id(tmp_path):
    # Greenhouse jobs now carry a stable id → the delta keys on it. A job whose id
    # was seen last run is filtered even though delta_only is on.
    store = tmp_path / ".last_ats_seen.json"
    payload = {
        "jobs": [
            {
                "title": "Backend Team Lead",
                "id": 555,
                "location": {"name": "Netanya, Israel"},
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/555",
                "updated_at": _recent_iso(),
                "content": "<p>Python backend AI agent.</p>",
            }
        ]
    }
    ats_ingest.save_seen(store, [{"title": "whatever", "company": "acme", "job_id": "555"}])
    targets = [{"company": "Acme", "ats": "greenhouse", "token": "acme"}]
    jobs, _ = ats_ingest.build_positions(
        targets,
        prev_keys=ats_ingest.load_seen(store),
        delta_only=True,
        get_json=lambda url: payload,
    )
    assert jobs == []  # id already seen → delta filters it


# --- shortlist + liveness ----------------------------------------------------


class _Live:
    def __init__(self, status):
        self.status = status


def test_shortlist_drops_closed_keeps_open(tmp_path):
    cfg = _write_config(tmp_path, [{"company": "Acme", "ats": "greenhouse", "token": "acme"}])
    payload = _gh_jobs(("Backend Team Lead", _recent_iso()), ("AI Team Lead", _recent_iso()))

    def fake_liveness(url):
        return _Live("CLOSED" if url.endswith("/0") else "OPEN")

    jobs, _ = ats_shortlist.build_ats_shortlist(
        cfg,
        from_scratch=True,
        seen_store=tmp_path / "seen.json",
        get_json=lambda url: payload,
        liveness_check=fake_liveness,
    )
    assert len(jobs) == 1
    assert jobs[0]["liveness"] == "OPEN"
    assert jobs[0]["url"].endswith("/1")


def test_shortlist_no_liveness_flag(tmp_path):
    cfg = _write_config(tmp_path, [{"company": "Acme", "ats": "greenhouse", "token": "acme"}])
    payload = _gh_jobs(("Backend Team Lead", _recent_iso()))
    jobs, _ = ats_shortlist.build_ats_shortlist(
        cfg,
        from_scratch=True,
        do_liveness=False,
        seen_store=tmp_path / "seen.json",
        get_json=lambda url: payload,
    )
    assert jobs[0]["liveness"] == "skipped"


# --- LISTED: the fourth liveness state ---------------------------------------
# All 24 Comeet rows on the 2026-08-05 board read UNVERIFIED while being demonstrably
# live: their apply pages render status client-side, so no HTTP checker can read one. The
# API returning the req IS the authoritative evidence, so it earns its own state rather
# than being collapsed into OPEN (which would flip a binary rule on softer evidence) or
# left at UNVERIFIED (which trained the reader to ignore the marker entirely).


def test_unverified_api_row_is_upgraded_to_listed(tmp_path):
    cfg = _write_config(tmp_path, [{"company": "Acme", "ats": "greenhouse", "token": "acme"}])
    payload = _gh_jobs(("Backend Team Lead", _recent_iso()))
    jobs, _ = ats_shortlist.build_ats_shortlist(
        cfg,
        from_scratch=True,
        seen_store=tmp_path / "seen.json",
        get_json=lambda url: payload,
        liveness_check=lambda u: _Live("UNVERIFIED"),
    )
    assert jobs[0]["liveness"] == "LISTED"


def test_a_closed_verdict_is_never_upgraded(tmp_path):
    """The safety property: LISTED may only ever replace UNVERIFIED. Believing the API over
    a CLOSED verdict would resurrect dead postings on every poll."""
    cfg = _write_config(tmp_path, [{"company": "Acme", "ats": "greenhouse", "token": "acme"}])
    payload = _gh_jobs(("Backend Team Lead", _recent_iso()))
    jobs, _ = ats_shortlist.build_ats_shortlist(
        cfg,
        from_scratch=True,
        seen_store=tmp_path / "seen.json",
        get_json=lambda url: payload,
        liveness_check=lambda u: _Live("CLOSED"),
    )
    assert jobs == [], "a CLOSED posting must still be dropped"


def test_an_open_verdict_is_not_downgraded_to_listed(tmp_path):
    cfg = _write_config(tmp_path, [{"company": "Acme", "ats": "greenhouse", "token": "acme"}])
    payload = _gh_jobs(("Backend Team Lead", _recent_iso()))
    jobs, _ = ats_shortlist.build_ats_shortlist(
        cfg,
        from_scratch=True,
        seen_store=tmp_path / "seen.json",
        get_json=lambda url: payload,
        liveness_check=lambda u: _Live("OPEN"),
    )
    assert jobs[0]["liveness"] == "OPEN"


def test_linkedin_rows_are_not_upgraded(tmp_path):
    """LinkedIn is a guest scrape of rendered search cards — stale and rate-limited — so it
    earns no upgrade. Only real JSON APIs do."""
    from src.jobcore.ats_shortlist import _api_listed_or

    assert _api_listed_or({"source": "linkedin:Rafael"}, "UNVERIFIED") == "UNVERIFIED"
    assert _api_listed_or({"source": "comeet:Cyera"}, "UNVERIFIED") == "LISTED"
    assert _api_listed_or({"source": "greenhouse:Aidoc"}, "UNVERIFIED") == "LISTED"
    assert _api_listed_or({"source": ""}, "UNVERIFIED") == "UNVERIFIED"


def test_market_scan_builds_shortlist_from_all_companies(tmp_path):
    # Company-agnostic market scan: injected search returns raw roles at arbitrary
    # companies (not in any config); they flow through the same scoring + liveness.
    def fake_search(keywords, location="Israel"):
        return [
            {
                "title": "AI-Driven Full Stack Team Lead",
                "company": "Cellxpert",
                "location": "Tel Aviv, Israel",
                "domain": "Software",
                "url": "https://www.linkedin.com/jobs/view/4438471660",
                "snippet": "AI agents Python backend team lead",
                "posted_date": date.today(),
                "job_id": "4438471660",
                "source": "linkedin:Cellxpert",
            }
        ]

    jobs, _ = ats_shortlist.build_ats_shortlist(
        tmp_path / "no_config.json",  # config intentionally absent — market ignores it
        from_scratch=True,
        seen_store=tmp_path / "market_seen.json",
        market_keywords=["AI full stack team lead"],
        market_search=fake_search,
        liveness_check=lambda u: _Live("OPEN"),
    )
    assert len(jobs) == 1
    assert jobs[0]["company"] == "Cellxpert"
    assert jobs[0]["in_preferred_geo"] == "Yes"  # Tel Aviv → commute band
    assert jobs[0]["liveness"] == "OPEN"


def test_shortlist_markdown_has_columns(tmp_path):
    jobs = [
        {
            "match_score": 9,
            "liveness": "OPEN",
            "company": "Acme",
            "title": "AI Team Lead",
            "location": "Netanya",
            "in_preferred_geo": "Yes",
            "posted_str": "2026-07-10",
            "source": "greenhouse:Acme",
            "url": "https://x/1",
        }
    ]
    md = ats_shortlist._to_markdown(jobs)
    assert "Live" in md and "Source" in md and "AI Team Lead" in md


def test_save_seen_accumulates_across_runs(tmp_path):
    """Regression: save_seen overwrote the store with only the current run's keys, so the delta
    only ever compared against the PREVIOUS run. A role seen two runs ago re-surfaced as new."""
    store = tmp_path / "seen.json"
    save_seen(store, [{"title": "Backend TL", "company": "Acme", "job_id": "1"}])
    assert load_seen(store) == {("id", "1")}

    save_seen(store, [{"title": "AI TL", "company": "Beta", "job_id": "2"}])
    keys = load_seen(store)
    assert keys == {("id", "1"), ("id", "2")}, f"run 1's key must survive run 2, got {keys}"
