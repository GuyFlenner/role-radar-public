"""LinkedIn rate-limit handling — a block must never look like an exhausted board.

Regression cover for the 2026-08-03 defect: the guest endpoint 429'd partway
through a multi-keyword sweep, ``http_get_text`` mapped that to "", and
``fetch``'s ``if not jobs: break`` read it as "no more pages". Elbit (a focus
target) silently lost 4 keyword pages and the run reported as complete.

Every test injects sleep/rand, so nothing here actually waits and the backoff
schedule is asserted exactly.
"""

import urllib.error

from src.jobcore.ats import linkedin
from src.jobcore.ats.base import BLOCK_STATUSES, http_get_text_status

_CARD = """
<div class="base-card" data-entity-urn="urn:li:jobPosting:{jid}">
  <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/{jid}"></a>
  <h3 class="base-search-card__title">Software Team Lead</h3>
  <h4 class="base-search-card__subtitle">Elbit Systems</h4>
  <span class="job-search-card__location">Haifa, Israel</span>
</div>
"""


def _page(n, offset=0):
    """n cards with distinct job ids. ``offset`` keeps pages from deduping into each
    other — the provider dedups by job_id, so reused ids hide real page loss."""
    return "".join(_CARD.format(jid=440000000 + offset + i) for i in range(n))


class _Recorder:
    """Injectable sleep/rand that records delays instead of waiting."""

    def __init__(self):
        self.delays = []

    def sleep(self, d):
        self.delays.append(round(d, 3))

    def rand(self):
        return 0.0  # no jitter → exact, assertable delays


def _target(keywords=("software team lead",)):
    return {"company": "Elbit Systems", "company_id": "163594", "keywords": list(keywords)}


# ------------------------------------------------------- block vs exhaustion


def test_block_is_reported_as_truncation_not_end_of_results():
    """The core regression: a 429 must be distinguishable from an empty page."""
    r, report = _Recorder(), []
    calls = []

    def getter(url):
        calls.append(url)
        return (_page(3), 200) if "start=0" in url else ("", 429)

    jobs = linkedin.fetch(_target(), get_text=getter, sleep=r.sleep, rand=r.rand, report=report, max_retries=2)
    assert len(jobs) == 3  # page 1 kept
    assert len(report) == 1
    assert report[0]["reason"] == "rate_limited"
    assert report[0]["keyword"] == "software team lead"
    assert report[0]["page"] == 2


def test_genuinely_empty_page_ends_keyword_without_truncation_report():
    r, report = _Recorder(), []

    def getter(url):
        return (_page(3), 200) if "start=0" in url else ("", 200)

    jobs = linkedin.fetch(_target(), get_text=getter, sleep=r.sleep, rand=r.rand, report=report)
    assert len(jobs) == 3
    assert report == []  # exhaustion is not truncation


def test_network_failure_is_not_treated_as_a_block():
    """status 0 (DNS/timeout) ends the keyword; retrying a dead host wastes the run."""
    r, report = _Recorder(), []
    jobs = linkedin.fetch(_target(), get_text=lambda u: ("", 0), sleep=r.sleep, rand=r.rand, report=report)
    assert jobs == []
    assert report == []


# ------------------------------------------------------- retry + recovery


def test_page_recovers_after_a_transient_block_no_data_lost():
    r = _Recorder()
    state = {"n": 0}

    def getter(url):
        if "start=10" in url:
            state["n"] += 1
            return ("", 429) if state["n"] == 1 else (_page(2, offset=100), 200)
        return (_page(3), 200) if "start=0" in url else ("", 200)

    jobs = linkedin.fetch(_target(), get_text=getter, sleep=r.sleep, rand=r.rand, max_retries=3)
    assert len(jobs) == 5  # 3 + 2 — the retried page was NOT lost
    assert 2.0 in r.delays  # one backoff happened


def test_backoff_is_exponential_and_capped_by_max_retries():
    r = _Recorder()
    linkedin.fetch(
        _target(),
        get_text=lambda u: ("", 429),
        sleep=r.sleep,
        rand=r.rand,
        max_retries=3,
    )
    # 3 attempts → 2 backoffs, doubling: 2s then 4s. No sleep after the last attempt.
    assert r.delays == [2.0, 4.0]


def test_jitter_widens_the_delay():
    r = _Recorder()
    r.rand = lambda: 1.0  # max jitter → double the base delay
    linkedin.fetch(_target(), get_text=lambda u: ("", 429), sleep=r.sleep, rand=r.rand, max_retries=2)
    assert r.delays == [4.0]  # 2.0 * 2**0 * (1 + 1.0)


def test_every_block_status_triggers_retry():
    for status in sorted(BLOCK_STATUSES):
        r, report = _Recorder(), []
        linkedin.fetch(
            _target(),
            get_text=lambda u, s=status: ("", s),
            sleep=r.sleep,
            rand=r.rand,
            report=report,
            max_retries=2,
        )
        assert r.delays, f"status {status} should back off"
        assert report and report[0]["reason"] == "rate_limited"


# ------------------------------------------------------- politeness pause


def test_pause_between_pages_but_not_before_the_first():
    r = _Recorder()

    def getter(url):
        return (_page(3), 200) if "start=0" in url or "start=10" in url else ("", 200)

    linkedin.fetch(_target(), get_text=getter, sleep=r.sleep, rand=r.rand)
    # pages fetched: start=0 (no pause), start=10 (pause), start=20 (pause) → 2 pauses
    assert r.delays == [0.4, 0.4]


# ------------------------------------------------------- back-compat


def test_legacy_body_only_getter_still_works():
    """Older callers/tests inject a plain str getter; behaviour must be unchanged."""
    r = _Recorder()
    jobs = linkedin.fetch(
        _target(),
        get_text=lambda u: _page(2) if "start=0" in url_of(u) else "",
        sleep=r.sleep,
        rand=r.rand,
    )
    assert len(jobs) == 2


def url_of(u):
    return u


def test_search_market_reports_truncation_too():
    r, report = _Recorder(), []
    jobs = linkedin.search_market(
        ["backend team lead"],
        get_text=lambda u: (_page(2), 200) if "start=0" in u else ("", 429),
        sleep=r.sleep,
        rand=r.rand,
        report=report,
        max_retries=2,
    )
    assert len(jobs) == 2
    assert report[0]["target"] == "market"


# ------------------------------------------------------- base getter


def test_http_get_text_status_surfaces_http_error_code(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.HTTPError("http://x", 429, "Too Many Requests", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", boom)
    body, status = http_get_text_status("http://x")
    assert (body, status) == ("", 429)


def test_http_get_text_status_maps_network_failure_to_zero(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert http_get_text_status("http://x") == ("", 0)


def test_http_get_text_still_returns_a_plain_string(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    from src.jobcore.ats.base import http_get_text

    assert http_get_text("http://x") == ""
