"""Contract tests between static/board.html and the view model it consumes.

The page is plain JS with no build step and no type checker, so a renamed view-model field would
fail silently in the browser as `undefined` — the strip would render, just wrong. These tests are
the type check: every field the page reads must be one the view actually emits, and every element
the script looks up must exist in the markup.

Also asserts the page stays self-contained. It is served over loopback to a page showing
head-hunter names and rejection notes; a stray CDN reference would leak referrer data off-machine.
"""

import re
from pathlib import Path

import pytest

from src.jobcore import board_server, board_view, pipeline
from src.jobcore import company_snapshot as profile

PAGE = Path(board_server.PAGE).read_text(encoding="utf-8")
SCRIPT = PAGE.split("<script>", 1)[1].rsplit("</script>", 1)[0]


@pytest.fixture
def card():
    led = pipeline.load(Path("nonexistent-for-skeleton.json"))
    pipeline.upsert(
        led,
        [
            {
                "company": "Armis",
                "title": "AI Engineering Team Lead",
                "job_id": "a1",
                "url": "https://example.test/a1",
                "location": "Tel Aviv",
                "match_score": 9,
                "level_band": "in_band",
                "source": "linkedin",
            }
        ],
        "2026-08-06",
    )
    return board_view.view(led, today="2026-08-06")["cards"][0]


def test_every_card_field_the_page_reads_is_emitted_by_the_view(card):
    """A field renamed in board_view.py without updating the page must fail here."""
    read = set(re.findall(r"\bc\.([a-z_]+)\b", SCRIPT))
    read -= {"closest", "id"}  # DOM helpers, and `id` is checked below with the rest

    missing = read - set(card)

    assert not missing, f"page reads card fields the view model does not emit: {sorted(missing)}"


def test_every_top_level_field_the_page_reads_is_emitted_by_the_view():
    led = pipeline.load(Path("nonexistent-for-skeleton.json"))
    payload = board_view.view(led, today="2026-08-06")

    read = set(re.findall(r"\bd(?:ata)?\.([a-z_]+)\b", SCRIPT))

    assert read <= set(payload), f"page reads unknown top-level fields: {sorted(read - set(payload))}"


def test_the_pages_bucket_list_matches_the_view_models_buckets():
    declared = set(re.search(r"const BUCKETS = \[(.*?)\]", SCRIPT, re.S).group(1).replace('"', "").split(","))
    declared = {b.strip() for b in declared if b.strip()}

    led = pipeline.load(Path("nonexistent-for-skeleton.json"))

    assert declared == set(board_view.view(led)["counts"])


def test_every_alarm_field_the_page_reads_is_emitted(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(
        led,
        [{"company": "Axon", "title": "EM", "job_id": "x1", "url": "u", "match_score": 8, "source": "s"}],
        "2026-07-30",
    )
    pipeline.add_channel(led, opp_id="axon:em", channel="head_hunter", name="Noa", today="2026-07-30")

    alarm = board_view.view(led, today="2026-08-06")["alarms"][0]
    read = set(re.findall(r"\ba\.([a-z_]+)\b", SCRIPT))

    assert read <= set(alarm), f"page reads unknown alarm fields: {sorted(read - set(alarm))}"


def test_every_element_the_script_looks_up_exists_in_the_markup():
    looked_up = set(re.findall(r"""\$\(["']([\w-]+)["']\)""", SCRIPT))
    present = set(re.findall(r'id="([\w-]+)"', PAGE))

    assert looked_up <= present, f"script looks up missing ids: {sorted(looked_up - present)}"


def test_the_page_loads_nothing_from_the_network():
    """Self-contained: the ledger carries head-hunter names, so nothing may leave the machine."""
    for pattern in ("cdn.", "googleapis", "unpkg", "jsdelivr", "cdnjs"):
        assert pattern not in PAGE

    remote = re.findall(r'(?:src|href)\s*=\s*["\'](https?:)?//', PAGE)

    assert not remote, "page references a remote resource"


def test_the_page_calls_only_the_read_only_endpoints():
    """Both endpoints are declared as constants, and every fetch uses one of them.

    Asserting on the *call sites* rather than on string literals matters: a `fetch(`...`)` built
    from a template literal would slip past a literal-only regex, which is exactly how a third
    endpoint would arrive unnoticed.
    """
    declared = dict(re.findall(r"""const (BOARD_URL|DETAIL_URL) = ["']([^"']+)["']""", SCRIPT))

    assert declared == {"BOARD_URL": "/api/board", "DETAIL_URL": "/api/position/"}

    call_args = [m.strip() for m in re.findall(r"fetch\(([^)]*)\)", SCRIPT)]

    assert call_args, "no fetch call found — the page must still load the board"
    for arg in call_args:
        assert arg.split()[0].rstrip(",") in declared, f"fetch does not use a declared endpoint: {arg!r}"
    assert "method:" not in SCRIPT, "fetch must stay a plain GET — the board is read-only"


def test_the_detail_endpoint_matches_the_servers_route():
    """The page and the server must agree on the prefix, or every drawer 404s."""
    declared = re.search(r"""const DETAIL_URL = ["']([^"']+)["']""", SCRIPT).group(1)

    assert declared == board_server.DETAIL_PREFIX


def test_every_detail_field_the_page_reads_is_emitted_by_the_view(tmp_path):
    """The same type check the card payload gets, for the lazily-fetched detail payload."""
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(
        led,
        [{"company": "Payoneer", "title": "Eng Leader", "job_id": "p1", "url": "u", "match_score": 8, "source": "s"}],
        "2026-08-07",
    )
    payload = board_view.detail(led, "payoneer:eng-leader", today="2026-08-07")

    read = set(re.findall(r"\bdt\.([a-z_]+)\b", SCRIPT))

    assert read <= set(payload), f"page reads unknown detail fields: {sorted(read - set(payload))}"


def test_every_profile_field_the_page_reads_is_in_the_profile_schema():
    read = set(re.findall(r"\bpr\.([a-z_]+)\b", SCRIPT))

    assert read <= set(profile.ALLOWED_FIELDS), (
        f"page reads profile fields the schema does not declare: {sorted(read - set(profile.ALLOWED_FIELDS))}"
    )


def test_the_detail_cache_is_dropped_when_the_ledger_moves():
    """Regression, Phase 6 review (Major).

    `state.detail` is a cache keyed by id with no expiry. Without an explicit clear on every
    ledger-mtime change, an open panel showed the pre-event timeline for the rest of the browser
    session — defeating the one workflow the feature exists for: record a round, see it.
    """
    poll_body = SCRIPT.split("async function poll()", 1)[1].split("function markChanges", 1)[0]

    assert "state.detail.clear()" in poll_body, "poll() must drop stale detail payloads"
    assert "loadDetail(id)" in poll_body, "poll() must refetch the panels that are open"


def test_the_detail_panel_never_renders_a_document_body():
    """`documents` carries name/kind only; a page reading `.content`/`.path` means the server grew
    a leak the API tests would also catch — this is the second lock on the same door."""
    for forbidden in ("f.content", "f.body", "f.text", "f.path", "doc.content", "doc.path"):
        assert forbidden not in SCRIPT


def test_untrusted_ledger_text_is_escaped_before_it_reaches_the_dom():
    """Rejection notes and role titles are free text; they must never be injected raw."""
    assert "esc(" in SCRIPT
    for field in ("c.company", "c.role", "c.route", "c.open_url", "c.open_label"):
        assert f"esc({field})" in SCRIPT, f"{field} is interpolated without escaping"


# --- the card's action must be real ------------------------------------------


def test_the_open_action_is_a_real_anchor_bound_to_the_view_model():
    """The first version rendered the words 'open the link' as prose, with nothing behind them."""
    assert 'href="${esc(c.open_url)}"' in SCRIPT
    assert "esc(c.open_label)" in SCRIPT
    assert 'data-kind="${esc(c.open_kind)}"' in SCRIPT


def test_no_prose_pretends_to_be_a_link():
    assert "open the link" not in SCRIPT


def test_the_action_label_is_never_hardcoded():
    """`apply` sat on Amdocs/AI Evangelist Lead while that posting was closed."""
    assert "apply&nbsp;" not in SCRIPT
    assert ">apply<" not in SCRIPT.lower()


def test_outbound_links_do_not_leak_the_referrer():
    for anchor in re.findall(r"<a\b[^>]*target=\"_blank\"[^>]*>", SCRIPT):
        assert "noopener" in anchor and "noreferrer" in anchor


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))


# --- recording a liveness verdict from the drawer ----------------------------


def test_the_drawer_offers_every_command_the_view_model_emits(card):
    """A new command added to board_view must actually reach a button."""
    wired = set(re.findall(r'\["(\w+)",\s*"[^"]*"\]', SCRIPT))

    assert set(card["commands"]) == wired, f"commands not wired to a button: {sorted(set(card['commands']) - wired)}"


def test_liveness_and_decision_buttons_stay_separate():
    """A dead posting is a market fact, never a decision to stop pursuing."""
    live = re.search(r"const LIVE_CMDS = \[(.*?)\];", SCRIPT, re.S).group(1)
    decision = re.search(r"const DECISION_CMDS = \[(.*?)\];", SCRIPT, re.S).group(1)

    assert set(re.findall(r'"(live_\w+)"', live)) == {"live_open", "live_listed", "live_closed"}
    assert "live_" not in decision


def test_the_drawer_shows_whether_anyone_has_checked_the_posting():
    assert "c.checked" in SCRIPT
    assert "esc(c.checked.status)" in SCRIPT


# --- the docs must describe the code that exists -----------------------------

DOC = Path("docs/PIPELINE_BOARD.md").read_text(encoding="utf-8")


def test_the_doc_quotes_the_real_defaults():
    from src.jobcore import board_server as bs

    assert f"`{bs.DEFAULT_PORT}`" in DOC
    assert f"http://{bs.HOST}:{bs.DEFAULT_PORT}" in DOC
    assert f"({pipeline.CHECK_FRESH_DAYS})" in DOC
    assert f"({pipeline.UNSENT_GRACE_DAYS})" in DOC


def test_the_doc_lists_every_bucket_and_liveness_value():
    led = pipeline.load(Path("nonexistent-for-skeleton.json"))

    for bucket in board_view.view(led)["counts"]:
        assert f"`{bucket}`" in DOC, f"bucket {bucket} is undocumented"
    for status in pipeline.LIVENESS:
        assert f"`{status}`" in DOC, f"liveness value {status} is undocumented"


def test_the_doc_names_every_js_rendered_host():
    for host in pipeline.JS_RENDERED_HOSTS:
        assert host in DOC, f"{host} is undocumented"


def test_the_doc_shows_every_flag_the_cli_accepts():
    from src.jobcore import board_server as bs

    parser_src = Path(bs.__file__).read_text(encoding="utf-8")
    flags = set(re.findall(r'add_argument\(\s*"(--[\w-]+)"', parser_src))

    for flag in flags:
        assert f"`{flag}" in DOC, f"{flag} is undocumented"


def test_the_doc_names_every_route_the_server_answers():
    """A route added without a doc line is how the doc started lying last time."""
    src = Path(board_server.__file__).read_text(encoding="utf-8")
    routes = set(re.findall(r'route == "([^"]+)"', src))
    routes.add(board_server.DETAIL_PREFIX)

    for route in routes:
        assert route in DOC, f"route {route} is undocumented"


def test_the_doc_lists_every_event_type():
    for event in pipeline.EVENTS:
        assert f"`{event}`" in DOC, f"event type {event} is undocumented"


def test_the_doc_describes_the_profile_schema():
    """Every servable profile field is named, so the closed schema is discoverable."""
    for field in profile.ALLOWED_FIELDS:
        if field == "schema_version":
            continue
        assert field in DOC, f"profile field {field} is undocumented"


def test_the_readme_points_at_the_full_doc():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "docs/PIPELINE_BOARD.md" in readme
    assert "src.jobcore.board_server" in readme


def test_the_board_doc_is_not_swallowed_by_gitignore():
    """Regression: a bare ``PIPELINE_BOARD.md`` ignore pattern matched docs/PIPELINE_BOARD.md too,
    so the doc this module's tests read at import time never reached a clean checkout."""
    import subprocess

    r = subprocess.run(["git", "check-ignore", "-q", "docs/PIPELINE_BOARD.md"], capture_output=True)
    assert r.returncode == 1, "docs/PIPELINE_BOARD.md is gitignored — CI will not see it"
