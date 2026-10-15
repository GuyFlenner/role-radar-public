"""Liveness-check tests — fully offline (fetch is injected/mocked).

Run: uv run pytest src/jobcore/tests/test_liveness.py -q
"""

from src.jobcore.liveness import (
    FetchResult,
    check_url,
    linkedin_job_id,
    resolve,
)

# --- URL parsing / resolution ------------------------------------------------


def test_linkedin_job_id_from_view_url():
    assert linkedin_job_id("https://www.linkedin.com/jobs/view/4375575099/") == "4375575099"


def test_linkedin_job_id_from_slugged_url():
    url = "https://il.linkedin.com/jobs/view/full-stack-team-leader-at-examplecorp-4375165974"
    assert linkedin_job_id(url) == "4375165974"


def test_linkedin_job_id_from_query_param():
    url = "https://www.linkedin.com/jobs/search/?currentJobId=4375556725&keywords=x"
    assert linkedin_job_id(url) == "4375556725"


def test_linkedin_job_id_none_for_non_linkedin():
    assert linkedin_job_id("https://www.devjobs.co.il/job-details/4375575099") is None


def test_resolve_linkedin_to_guest_endpoint():
    resolved, source = resolve("https://www.linkedin.com/jobs/view/4375575099/")
    assert resolved == "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4375575099"
    assert source == "linkedin-guest"


def test_resolve_direct_for_non_linkedin():
    url = "https://boards.greenhouse.io/acme/jobs/123"
    resolved, source = resolve(url)
    assert (resolved, source) == (url, "direct")


def test_resolve_aggregator_source():
    url = "https://www.devjobs.co.il/job-details/4375556725"
    resolved, source = resolve(url)
    assert (resolved, source) == (url, "aggregator")


# --- Classification via injected fetch ---------------------------------------


def _fetch_stub(result: FetchResult):
    return lambda _url: result


def test_closed_page_is_closed():
    fr = FetchResult(http=200, text="<h2>No longer accepting applications</h2>")
    r = check_url("https://www.linkedin.com/jobs/view/1/", fetch=_fetch_stub(fr))
    assert r.status == "CLOSED"


def test_open_page_is_open():
    fr = FetchResult(http=200, text="<button>Apply now</button> Submit your resume")
    r = check_url("https://example.com/job/1", fetch=_fetch_stub(fr))
    assert r.status == "OPEN"


def test_aggregator_open_is_downgraded_to_unverified():
    # Regression (Elbit req 4375556725, 2026-07-24): DevJobs kept a stale "apply" button
    # after LinkedIn had closed the role. An aggregator OPEN must NOT read as OPEN — it is
    # downgraded to UNVERIFIED so the digest can't over-report a closed role as live.
    fr = FetchResult(http=200, text="<button>Apply now</button> Submit your application")
    r = check_url("https://www.devjobs.co.il/job-details/4375556725", fetch=_fetch_stub(fr))
    assert r.status == "UNVERIFIED"
    assert "aggregator" in r.signal


def test_aggregator_closed_still_closed():
    # Trust CLOSED even from an aggregator — only OPEN is downgraded.
    fr = FetchResult(http=200, text="<h2>No longer accepting applications</h2>")
    r = check_url("https://www.devjobs.co.il/job-details/1", fetch=_fetch_stub(fr))
    assert r.status == "CLOSED"


def test_canonical_direct_open_stays_open():
    # A canonical ATS (Greenhouse/company) OPEN is authoritative — NOT downgraded.
    fr = FetchResult(http=200, text="<button>Apply now</button> Submit your resume")
    r = check_url("https://boards.greenhouse.io/acme/jobs/123", fetch=_fetch_stub(fr))
    assert r.status == "OPEN"


def test_block_is_unverified_not_closed():
    # The archived checker's bug: 403 must NOT read as CLOSED.
    fr = FetchResult(http=403, text="", blocked=True)
    r = check_url("https://www.linkedin.com/jobs/view/1/", fetch=_fetch_stub(fr))
    assert r.status == "UNVERIFIED"
    assert "blocked" in r.signal


def test_http_404_is_closed():
    fr = FetchResult(http=404, text="")
    r = check_url("https://example.com/job/gone", fetch=_fetch_stub(fr))
    assert r.status == "CLOSED"


def test_js_shell_is_unverified():
    fr = FetchResult(http=200, text="<div id=root>{{loading}}</div>")
    r = check_url("https://example.com/spa", fetch=_fetch_stub(fr))
    assert r.status == "UNVERIFIED"


def test_fetch_error_is_unverified():
    fr = FetchResult(http=None, text="", error="timed out")
    r = check_url("https://example.com/x", fetch=_fetch_stub(fr))
    assert r.status == "UNVERIFIED"
    assert "fetch error" in r.signal
