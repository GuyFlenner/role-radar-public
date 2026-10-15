"""ATS provider tests — pure parse() + injected fetch, fully offline."""

from datetime import date

from src.jobcore.ats import comeet, greenhouse
from src.jobcore.ats.base import iso_date, strip_html

# --- base helpers ------------------------------------------------------------


def test_iso_date_variants():
    assert iso_date("2026-07-01T12:00:00Z") == date(2026, 7, 1)
    assert iso_date("2026-07-01") == date(2026, 7, 1)
    assert iso_date("") is None
    assert iso_date(None) is None
    assert iso_date("not-a-date") is None


def test_strip_html_collapses_and_truncates():
    assert strip_html("<p>Hello&nbsp;<b>world</b></p>") == "Hello world"
    assert len(strip_html("<p>" + "x" * 999 + "</p>", limit=50)) == 50


# --- Greenhouse --------------------------------------------------------------


def _gh_payload():
    return {
        "jobs": [
            {
                "title": "Backend Team Lead",
                "location": {"name": "Netanya, Israel"},
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                "updated_at": "2026-07-10T09:00:00Z",
                "content": "<p>Lead a <b>Python</b> backend team building microservices.</p>",
            },
            {"title": "", "absolute_url": "x"},  # no title → dropped
        ]
    }


def test_greenhouse_parse_maps_fields():
    jobs = greenhouse.parse("Acme", _gh_payload())
    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Backend Team Lead"
    assert j["company"] == "Acme"
    assert j["location"] == "Netanya, Israel"
    assert j["url"] == "https://boards.greenhouse.io/acme/jobs/1"
    assert j["posted_date"] == date(2026, 7, 10)
    assert j["source"] == "greenhouse:Acme"
    assert "microservices" in j["snippet"] and "<" not in j["snippet"]


def test_greenhouse_parse_empty_payload():
    assert greenhouse.parse("Acme", None) == []
    assert greenhouse.parse("Acme", {}) == []


def test_greenhouse_fetch_uses_injected_getter():
    seen = {}

    def fake_get(url):
        seen["url"] = url
        return _gh_payload()

    jobs = greenhouse.fetch({"company": "Acme", "token": "acme"}, get_json=fake_get)
    assert seen["url"] == "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
    assert jobs[0]["company"] == "Acme"


def test_greenhouse_fetch_no_token():
    assert greenhouse.fetch({"company": "Acme"}, get_json=lambda u: _gh_payload()) == []


# --- Comeet ------------------------------------------------------------------


def _comeet_payload():
    return [
        {
            "name": "AI Engineering Team Lead",
            "location": {"name": "Herzliya, Israel"},
            "department": "R&D",
            "url_comeet_hosted_page": "https://www.comeet.co/jobs/globex/AA/ai-lead",
            "time_updated": "2026-07-08T00:00:00Z",
            "details": [{"value": "<p>Own the <b>LLM</b> platform.</p>"}],
        }
    ]


def test_comeet_parse_maps_fields():
    jobs = comeet.parse("Globex", _comeet_payload())
    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "AI Engineering Team Lead"
    assert j["location"] == "Herzliya, Israel"
    assert j["domain"] == "R&D"
    assert j["url"].endswith("/ai-lead")
    assert j["posted_date"] == date(2026, 7, 8)
    assert j["source"] == "comeet:Globex"
    assert "LLM" in j["snippet"]


def test_comeet_parse_non_list_payload():
    assert comeet.parse("Globex", {"error": "nope"}) == []
    assert comeet.parse("Globex", None) == []


def test_comeet_fetch_no_uid_or_token():
    assert comeet.fetch({"company": "Globex", "uid": "AA"}, get_json=lambda u: _comeet_payload()) == []
    assert comeet.fetch({"company": "Globex", "token": "t"}, get_json=lambda u: _comeet_payload()) == []
