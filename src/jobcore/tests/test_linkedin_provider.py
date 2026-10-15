"""LinkedIn guest job-search provider tests — pure parse() + injected fetch, offline."""

from datetime import date

from src.jobcore.ats import linkedin

# A trimmed but realistic LinkedIn guest job-search HTML fragment (two cards).
_HTML = """
<ul class="jobs-search__results-list">
  <li>
    <div class="base-card relative" data-entity-urn="urn:li:jobPosting:4377750552">
      <a class="base-card__full-link"
         href="https://il.linkedin.com/jobs/view/full-stack-team-lead-at-elbit-systems-4377750552?refId=abc&trackingId=xyz">
        <span class="sr-only">Full Stack Team Lead</span>
      </a>
      <h3 class="base-search-card__title">Full Stack Team Lead</h3>
      <h4 class="base-search-card__subtitle">Elbit Systems</h4>
      <span class="job-search-card__location">Netanya, Israel</span>
      <time class="job-search-card__listdate" datetime="2026-07-10">1 week ago</time>
    </div>
  </li>
  <li>
    <div class="base-card relative" data-entity-urn="urn:li:jobPosting:4390011223">
      <a class="base-card__full-link"
         href="https://www.linkedin.com/jobs/view/ai-team-lead-at-elbit-systems-4390011223">
        <span class="sr-only">AI Team Lead</span>
      </a>
      <h3 class="base-search-card__title">
        AI Team Lead
      </h3>
      <h4 class="base-search-card__subtitle">Elbit Systems</h4>
      <span class="job-search-card__location">Haifa, Israel</span>
    </div>
  </li>
</ul>
"""


def test_parse_maps_fields_and_job_id():
    jobs = linkedin.parse("Elbit Systems", _HTML)
    assert len(jobs) == 2
    j = jobs[0]
    assert j["title"] == "Full Stack Team Lead"
    assert j["company"] == "Elbit Systems"
    assert j["location"] == "Netanya, Israel"
    assert j["job_id"] == "4377750552"
    # apply URL is cleaned of tracking query params
    assert j["url"] == "https://il.linkedin.com/jobs/view/full-stack-team-lead-at-elbit-systems-4377750552"
    assert j["posted_date"] == date(2026, 7, 10)
    assert j["source"] == "linkedin:Elbit Systems"


def test_parse_handles_missing_date_and_collapses_title_whitespace():
    jobs = linkedin.parse("Elbit Systems", _HTML)
    j = jobs[1]
    assert j["title"] == "AI Team Lead"  # newlines/indentation collapsed
    assert j["location"] == "Haifa, Israel"
    assert j["posted_date"] is None  # no <time> → None (kept, never stale-dropped)


def test_parse_reconstructs_url_from_job_id_when_href_missing():
    # Some guest cards omit the base-card__full-link href; the apply URL must still
    # be usable (liveness + click-through) → reconstruct it from the job id.
    html = (
        '<div data-entity-urn="urn:li:jobPosting:4428589455">'
        '<h3 class="base-search-card__title">Integration Team Lead</h3>'
        '<span class="job-search-card__location">Holon, Israel</span></div>'
    )
    jobs = linkedin.parse("Elbit", html)
    assert len(jobs) == 1
    assert jobs[0]["url"] == "https://www.linkedin.com/jobs/view/4428589455"


def test_parse_empty_or_garbage():
    assert linkedin.parse("Elbit Systems", "") == []
    assert linkedin.parse("Elbit Systems", None) == []
    assert linkedin.parse("Elbit Systems", "<html><body>no cards here</body></html>") == []


def test_fetch_uses_injected_getter_and_builds_guest_url():
    seen = {}

    def fake_get_text(url):
        seen["url"] = url
        return _HTML

    jobs = linkedin.fetch(
        {"company": "Elbit Systems", "ats": "linkedin", "company_id": "12345", "location": "Israel"},
        get_text=fake_get_text,
    )
    assert "seeMoreJobPostings/search" in seen["url"]
    assert "f_C=12345" in seen["url"]
    assert "location=Israel" in seen["url"]
    assert len(jobs) == 2


def test_fetch_no_company_id():
    assert linkedin.fetch({"company": "Elbit Systems"}, get_text=lambda u: _HTML) == []


# --- company-agnostic market search ------------------------------------------

_MARKET_HTML = """
<ul>
  <li><div class="base-card" data-entity-urn="urn:li:jobPosting:4438471660">
    <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/ai-fs-tl-at-cellxpert-4438471660"></a>
    <h3 class="base-search-card__title">AI-Driven Full Stack Team Lead</h3>
    <h4 class="base-search-card__subtitle">Cellxpert</h4>
    <span class="job-search-card__location">Tel Aviv, Israel</span>
  </div></li>
  <li><div class="base-card" data-entity-urn="urn:li:jobPosting:4400000001">
    <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/eng-mgr-at-acme-4400000001"></a>
    <h3 class="base-search-card__title">Engineering Manager, AI</h3>
    <h4 class="base-search-card__subtitle">Acme AI</h4>
    <span class="job-search-card__location">Herzliya, Israel</span>
  </div></li>
</ul>
"""


def test_parse_extracts_per_card_company_when_arg_blank():
    # Market mode passes no company → the real company is read from each card.
    jobs = linkedin.parse("", _MARKET_HTML)
    assert {j["company"] for j in jobs} == {"Cellxpert", "Acme AI"}
    assert jobs[0]["source"] == "linkedin:Cellxpert"


def test_parse_prefers_card_company_over_arg():
    # Even targeted, the card's own company wins (arg is the fallback).
    jobs = linkedin.parse("SomeTarget", _MARKET_HTML)
    assert jobs[0]["company"] == "Cellxpert"


def test_search_market_builds_keyword_url_without_company_filter():
    calls = []

    def fake_get_text(url):
        calls.append(url)
        return _MARKET_HTML if "start=0" in url else ""

    jobs = linkedin.search_market(
        ["AI full stack team lead", "engineering manager AI"],
        location="Israel",
        get_text=fake_get_text,
    )
    # no company filter in the URL, keywords + location present
    assert all("f_C=" not in u for u in calls)
    assert any("keywords=AI" in u for u in calls) and any("location=Israel" in u for u in calls)
    # both keywords queried; results deduped by job_id across keywords
    assert {j["job_id"] for j in jobs} == {"4438471660", "4400000001"}
    assert {j["company"] for j in jobs} == {"Cellxpert", "Acme AI"}


def test_fetch_keywords_and_pagination_dedup():
    # Big employers post many roles; the guest endpoint pages ~10 at a time and
    # takes a keyword filter. fetch() should query each keyword, page until empty,
    # and dedup by job_id across keywords/pages.
    calls = []

    def fake_get_text(url):
        calls.append(url)
        return _HTML if "start=0" in url else ""  # only the first page has results

    jobs = linkedin.fetch(
        {"company": "Elbit", "company_id": "163594", "keywords": ["software team lead", "engineering manager"]},
        get_text=fake_get_text,
    )
    # two keyword phrases were queried, each url-encoded into the request
    assert any("keywords=software" in u for u in calls)
    assert any("keywords=engineering" in u for u in calls)
    # each keyword paged past page 0 at least once (start advanced), then stopped on empty
    assert any("start=0" in u for u in calls) and any("start=1" in u or "start=2" in u for u in calls)
    # same two job-ids returned by both keywords → deduped to 2
    assert len(jobs) == 2
    assert {j["job_id"] for j in jobs} == {"4377750552", "4390011223"}


def test_fetch_block_returns_empty():
    # A block (403/429/999) or any failure → get_text returns "" → no crash, no jobs.
    assert linkedin.fetch({"company": "Elbit", "company_id": "1"}, get_text=lambda u: "") == []
