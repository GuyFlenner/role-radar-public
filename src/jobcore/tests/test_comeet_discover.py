"""Comeet board-discovery tests — pure extract/parse/diff, fully offline.

The live scrape is exercised only through an injected ``get_text``; nothing here
touches the network. Ground truth for the token *shape* came from the four boards already in
ats_targets.json (Fetcherr/Cognyte/AllCloud/WSC Sports), but every token literal
below is SYNTHETIC. Real ones are served publicly by each careers page, yet they
still read as credentials — committing them trips the secrets gate, and rightly so.
"""

import json

from src.jobcore.ats import comeet_discover as cd

# A trimmed slice of a real Comeet careers page, with the token swapped for a fake.
# The three fields we need sit in one JSON blob; the surrounding noise is deliberately
# kept so the regex has to cope with neighbouring keys, not a hand-cleaned string.
_HTML = (
    '<script>window.COMEET_BOOT = {"allow_referral_outside_company": true, '
    '"referrals_reward": null, "is_reward": false, "careers_website_group_positions_by": 1, '
    '"company_uid": "68.006", "token": "DEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBE", '
    '"slug": "fetcherr", "is_customs_terms": false};</script>'
)


def test_extract_board_pulls_uid_token_slug():
    board = cd.extract_board(_HTML)
    assert board == {
        "slug": "fetcherr",
        "uid": "68.006",
        "token": "DEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBE",
    }


def test_extract_board_handles_short_token():
    """Token width varies across boards (28–38 hex chars seen live) — never assume one."""
    html = '{"company_uid": "41.00B", "token": "CAFEBABECAFEBABECAFEBABECAFE", "slug": "monday"}'
    assert cd.extract_board(html)["token"] == "CAFEBABECAFEBABECAFEBABECAFE"


def test_extract_board_falls_back_when_field_order_differs():
    """Comeet could reorder its JSON; uid+token alone are enough to poll."""
    html = '{"token": "AABBCCDDAABBCCDDAA", "unrelated": 1, "company_uid": "72.00F"}'
    board = cd.extract_board(html)
    assert board["uid"] == "72.00F"
    assert board["token"] == "AABBCCDDAABBCCDDAA"
    assert board["slug"] == ""  # unknown, and the caller already knows it


def test_extract_board_returns_none_on_junk():
    assert cd.extract_board("") is None
    assert cd.extract_board("<html><body>404 not found</body></html>") is None


def test_resolve_board_requests_slug_and_uid_url():
    """The uid MUST be in the path — /jobs/<slug> alone does not serve the blob."""
    seen = []

    def fake_get(url):
        seen.append(url)
        return _HTML

    board = cd.resolve_board("fetcherr", "68.006", get_text=fake_get)
    assert seen == ["https://www.comeet.com/jobs/fetcherr/68.006"]
    assert board["token"] == "DEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBE"


def test_resolve_board_returns_none_on_blocked_or_empty_fetch():
    assert cd.resolve_board("nope", "00.000", get_text=lambda _: "") is None


def test_resolve_board_prefers_input_slug_when_page_omits_it():
    html = '{"token": "AABBCCDDAABBCCDDAA", "company_uid": "72.00F"}'
    board = cd.resolve_board("thetaray", "72.00F", get_text=lambda _: html)
    assert board["slug"] == "thetaray"


def test_to_target_emits_exact_ats_targets_shape():
    board = {"slug": "vastdata", "uid": "43.001", "token": "BBAABBAABBAABBAABBAABBAABBAABBA"}
    assert cd.to_target(board, company="VAST Data") == {
        "company": "VAST Data",
        "ats": "comeet",
        "uid": "43.001",
        "token": "BBAABBAABBAABBAABBAABBAABBAABBA",
    }


def test_to_target_defaults_company_from_slug():
    board = {"slug": "wsc-sports", "uid": "93.007", "token": "CCDDCCDDCCDDCCDDCCDDCCDDCCDDCCDD"}
    assert cd.to_target(board)["company"] == "Wsc Sports"


# ---------------------------------------------------------------- URL harvesting


_SEARCH_BLOB = """
https://www.comeet.com/jobs/monday/41.00B/backend-team-lead/A1.234
Some prose in between, and a bare board link:
https://www.comeet.com/jobs/vastdata/43.001
http://comeet.com/jobs/upwind/49.004/engineering-manager/B2.567 — duplicate follows
https://www.comeet.com/jobs/monday/41.00B/another-role/C3.890
https://www.comeet.com/careers/not-a-board
https://www.comeet.com/jobs/UPWIND/49.004
"""


def test_parse_board_urls_extracts_unique_slug_uid_pairs():
    boards = cd.parse_board_urls(_SEARCH_BLOB)
    assert [(b["slug"], b["uid"]) for b in boards] == [
        ("monday", "41.00B"),
        ("vastdata", "43.001"),
        ("upwind", "49.004"),
    ]


def test_parse_board_urls_ignores_non_board_paths():
    assert cd.parse_board_urls("https://www.comeet.com/careers/foo https://example.com/jobs/x/1.000") == []


def test_parse_board_urls_handles_empty_input():
    assert cd.parse_board_urls("") == []


# ---------------------------------------------------------------- noise filter


def test_is_generic_title_flags_spark_hire_placeholders():
    assert cd.is_generic_title("Spark Hire Recruit Jobs")
    assert cd.is_generic_title("  spark hire recruit jobs  ")
    assert cd.is_generic_title("Comeet Jobs")
    assert cd.is_generic_title("")


def test_is_generic_title_keeps_real_roles():
    assert not cd.is_generic_title("Backend Team Lead")
    assert not cd.is_generic_title("Engineering Manager, Platform")


def test_parse_board_records_drops_placeholder_titled_hits():
    records = [
        {"title": "Backend Team Lead", "url": "https://www.comeet.com/jobs/monday/41.00B/x/1.1"},
        {"title": "Spark Hire Recruit Jobs", "url": "https://www.comeet.com/jobs/tikalk/5C.00D"},
        {"title": "", "url": "https://www.comeet.com/jobs/team8/61.003"},
    ]
    assert [b["slug"] for b in cd.parse_board_records(records)] == ["monday"]


def test_parse_board_records_tolerates_junk():
    assert cd.parse_board_records(None) == []
    assert cd.parse_board_records(["not a dict", {"title": "X"}]) == []


# ---------------------------------------------------------------- diffing


def _configured():
    return [
        {"company": "Fetcherr", "ats": "comeet", "uid": "68.006", "token": "x"},
        {"company": "Cato", "ats": "greenhouse", "token": "catonetworks"},
    ]


def test_new_boards_drops_already_configured_by_uid():
    found = [
        {"slug": "fetcherr", "uid": "68.006"},
        {"slug": "monday", "uid": "41.00B"},
    ]
    assert [b["slug"] for b in cd.new_boards(found, _configured())] == ["monday"]


def test_new_boards_ignores_non_comeet_targets():
    """A greenhouse token must never be mistaken for a comeet uid."""
    found = [{"slug": "catonetworks", "uid": "11.111"}]
    assert len(cd.new_boards(found, _configured())) == 1


def test_new_boards_drops_previously_seen():
    found = [{"slug": "monday", "uid": "41.00B"}, {"slug": "upwind", "uid": "49.004"}]
    seen = {("monday", "41.00B")}
    assert [b["slug"] for b in cd.new_boards(found, _configured(), seen=seen)] == ["upwind"]


# ---------------------------------------------------------------- delta store


def test_seen_store_roundtrips(tmp_path):
    store = tmp_path / ".last_comeet_discovery_seen.json"
    cd.save_seen_boards(store, [{"slug": "monday", "uid": "41.00B"}])
    assert cd.load_seen_boards(store) == {("monday", "41.00B")}


def test_seen_store_accumulates_never_overwrites(tmp_path):
    """Overwriting made the ats delta oscillate run-to-run; do not repeat that bug."""
    store = tmp_path / "seen.json"
    cd.save_seen_boards(store, [{"slug": "monday", "uid": "41.00B"}])
    cd.save_seen_boards(store, [{"slug": "upwind", "uid": "49.004"}])
    assert cd.load_seen_boards(store) == {("monday", "41.00B"), ("upwind", "49.004")}


def test_load_seen_boards_tolerates_missing_and_corrupt(tmp_path):
    assert cd.load_seen_boards(tmp_path / "nope.json") == set()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert cd.load_seen_boards(bad) == set()
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"a": 1}), encoding="utf-8")
    assert cd.load_seen_boards(wrong) == set()


# ---------------------------------------------------------------- CSV harvest


def test_harvest_from_text_finds_pairs_in_a_ranked_csv_blob():
    csv_blob = (
        "title,url\n"
        '"AI Data Team Lead","comeet.com/jobs/fetcherr/68.006/ai-data-team-lead/0C.563"\n'
        '"Backend TL","https://www.comeet.com/jobs/thetaray/72.00F/backend/1A.001"\n'
    )
    boards = cd.parse_board_urls(csv_blob)
    assert [(b["slug"], b["uid"]) for b in boards] == [("fetcherr", "68.006"), ("thetaray", "72.00F")]
