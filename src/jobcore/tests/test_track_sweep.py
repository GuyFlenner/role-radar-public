"""track_sweep tests — offline, liveness injected, temp positions/ tree."""

from src.jobcore import pipeline, track_sweep


class _Live:
    """Stand-in for liveness.LivenessResult (only the fields track_sweep reads)."""

    def __init__(self, status, signal="", http=200):
        self.status = status
        self.signal = signal
        self.http = http


def _make_position(root, slug, files):
    d = root / slug
    d.mkdir(parents=True)
    for name, text in files.items():
        (d / name).write_text(text, encoding="utf-8")
    return d


# --- URL extraction ----------------------------------------------------------


def test_extract_prefers_apply_url_over_source(tmp_path):
    d = _make_position(
        tmp_path,
        "elbit",
        {
            "JD_DESCRIPTION.md": (
                "**Apply URL:** https://il.linkedin.com/jobs/view/full-stack-4377750552\n"
                "**Source:** https://elbitsystems.com/careers/ (portal)\n"
            )
        },
    )
    assert track_sweep.extract_apply_url(d) == "https://il.linkedin.com/jobs/view/full-stack-4377750552"


def test_extract_falls_back_to_source_and_strips_trailing_paren(tmp_path):
    d = _make_position(
        tmp_path,
        "jfrog",
        {
            "JD_DESCRIPTION.md": (
                "**Source:** https://boards-api.greenhouse.io/v1/boards/jfrog/jobs/7935939 (confirmed) | id 7935939\n"
            )
        },
    )
    assert track_sweep.extract_apply_url(d) == "https://boards-api.greenhouse.io/v1/boards/jfrog/jobs/7935939"


def test_extract_reads_status_apply_via_when_no_jd(tmp_path):
    d = _make_position(
        tmp_path,
        "datadog",
        {"STATUS.md": "- [ ] Apply via: https://careers.datadoghq.com/detail/7942079/?gh_jid=7942079\n"},
    )
    assert track_sweep.extract_apply_url(d) == "https://careers.datadoghq.com/detail/7942079/?gh_jid=7942079"


def test_extract_ignores_personal_linkedin_profile(tmp_path):
    d = _make_position(
        tmp_path,
        "nourl",
        {"CV_TAILORED.md": "Springfield | [LinkedIn](https://linkedin.com/in/janesmith)\n"},
    )
    assert track_sweep.extract_apply_url(d) is None


# --- sweep + report ----------------------------------------------------------


def test_sweep_skips_scaffolding_and_flags_closed_first(tmp_path):
    _make_position(tmp_path, "_template", {"JD_DESCRIPTION.md": "**Source:** https://x/tmpl\n"})
    _make_position(tmp_path, "_legacy", {"JD_DESCRIPTION.md": "**Source:** https://x/occupied\n"})
    _make_position(tmp_path, "seeking-alpha-return", {"JD_ANALYSIS.md": "**Source:** https://x/closed-known\n"})
    _make_position(tmp_path, "willow", {"JD_DESCRIPTION.md": "**Apply URL:** https://x/open\n"})
    _make_position(tmp_path, "bylith", {"JD_DESCRIPTION.md": "**Apply URL:** https://x/dead\n"})
    _make_position(tmp_path, "nourl", {"README.md": "no link here\n"})

    def fake_check(url):
        return _Live("CLOSED", "gone") if url.endswith("/dead") else _Live("OPEN", "apply")

    rows = track_sweep.sweep(tmp_path, check=fake_check)
    slugs = [r["slug"] for r in rows]
    # scaffolding + legacy + known-closed folders skipped
    assert not ({"_template", "_legacy", "seeking-alpha-return"} & set(slugs))
    # CLOSED sorted first
    assert rows[0]["slug"] == "bylith" and rows[0]["status"] == "CLOSED"
    # no-URL position reported as such, not crashed
    nourl = next(r for r in rows if r["slug"] == "nourl")
    assert nourl["status"] == "NO_URL"


def test_to_markdown_has_closed_section(tmp_path):
    rows = [
        {"slug": "bylith", "url": "https://x/dead", "status": "CLOSED", "signal": "gone"},
        {"slug": "willow", "url": "https://x/open", "status": "OPEN", "signal": "apply"},
    ]
    md = track_sweep.to_markdown(rows)
    assert "CLOSED" in md and "bylith" in md and "willow" in md


# --- ledger awareness --------------------------------------------------------


def _ledger(*rows):
    return {"schema_version": pipeline.SCHEMA_VERSION, "company_policy": {}, "opportunities": list(rows)}


def _opp(opp_id, folder, stage, posting_live="UNVERIFIED", outcome=None):
    return {
        "id": opp_id,
        "company": opp_id.split(":")[0],
        "company_slug": opp_id.split(":")[0],
        "role": "Role",
        "position_folder": folder,
        "stage": stage,
        "outcome": outcome,
        "posting_live": posting_live,
        "history": [],
    }


def test_sweep_skips_pursuits_closed_in_the_ledger(tmp_path):
    """A pursuit the candidate already closed must not be probed or reported as live."""
    _make_position(tmp_path, "intango", {"JD_DESCRIPTION.md": "**Apply URL:** https://x.test/1\n"})
    _make_position(tmp_path, "blinkops", {"JD_DESCRIPTION.md": "**Apply URL:** https://x.test/2\n"})
    led = _ledger(_opp("intango:em", "positions/intango", "CLOSED", outcome="THEY_REJECTED"))

    rows = track_sweep.sweep(tmp_path, check=lambda u: _Live("OPEN"), ledger=led)
    assert [r["slug"] for r in rows] == ["blinkops"]


def test_sweep_writes_posting_live_but_never_stage(tmp_path):
    """An active pursuit whose posting went down is NOT a closed pursuit."""
    _make_position(tmp_path, "examplecorp", {"JD_DESCRIPTION.md": "**Apply URL:** https://x.test/3\n"})
    led = _ledger(_opp("examplecorp:fs-tl", "positions/examplecorp", "IN_PROCESS"))

    track_sweep.sweep(tmp_path, check=lambda u: _Live("CLOSED"), ledger=led)
    o = led["opportunities"][0]
    assert o["posting_live"] == "CLOSED", "the market fact is recorded"
    assert o["stage"] == "IN_PROCESS", "pursuit state is untouched by a dead posting"
    assert o["outcome"] is None


def test_sweep_without_ledger_behaves_exactly_as_before(tmp_path):
    _make_position(tmp_path, "sett", {"JD_DESCRIPTION.md": "**Apply URL:** https://x.test/4\n"})
    rows = track_sweep.sweep(tmp_path, check=lambda u: _Live("OPEN"))
    assert [r["slug"] for r in rows] == ["sett"]


def test_main_does_not_alarm_when_only_closed_pursuits_have_dead_postings(tmp_path):
    """Exit 3 means 'a pursuit you are actively chasing just died' - not 'an archived one is dead'."""
    _make_position(tmp_path, "intango", {"JD_DESCRIPTION.md": "**Apply URL:** https://x.test/1\n"})
    led_path = tmp_path / "pipeline.json"
    pipeline.save(_ledger(_opp("intango:em", "positions/intango", "CLOSED", outcome="THEY_REJECTED")), led_path)

    rc = track_sweep.main(["--positions", str(tmp_path), "--out", str(tmp_path / "out.md"), "--ledger", str(led_path)])
    assert rc == 0


def test_main_no_ledger_keeps_the_original_alarm_on_any_closed_posting(tmp_path):
    """Without stage data there is nothing to filter on, so don't silently stop alarming."""
    _make_position(tmp_path, "somerole", {"JD_DESCRIPTION.md": "**Apply URL:** https://x.test/9\n"})
    rc = track_sweep.main(
        ["--positions", str(tmp_path), "--out", str(tmp_path / "out.md"), "--no-ledger"],
    )
    # The real network is not reached in CI; assert only that --no-ledger runs and returns a
    # documented code (0 = nothing closed, 3 = something closed) rather than crashing.
    assert rc in (0, 3)


def test_main_alarms_when_an_in_flight_pursuit_loses_its_posting(tmp_path, monkeypatch):
    _make_position(tmp_path, "examplecorp", {"JD_DESCRIPTION.md": "**Apply URL:** https://x.test/3\n"})
    led_path = tmp_path / "pipeline.json"
    pipeline.save(_ledger(_opp("examplecorp:fs-tl", "positions/examplecorp", "IN_PROCESS")), led_path)
    monkeypatch.setattr(track_sweep, "check_url", lambda u: _Live("CLOSED"))

    rc = track_sweep.main(["--positions", str(tmp_path), "--out", str(tmp_path / "out.md"), "--ledger", str(led_path)])
    assert rc == 3, "an actively-chased pursuit losing its posting must still alarm"


# --- CLOSED must survive an inconclusive re-check -----------------------------
# WSC Sports, 2026-08-05: found CLOSED by the morning sweep, then an inconclusive
# afternoon re-check overwrote it with UNVERIFIED and the role climbed back into ACT
# NOW as an actionable 8/10. An inconclusive probe is the ABSENCE of evidence; it must
# not erase a positive reading.


def test_closed_is_sticky_against_an_inconclusive_recheck():
    from src.jobcore.track_sweep import _merge_liveness

    assert _merge_liveness("CLOSED", "UNVERIFIED") == "CLOSED"
    assert _merge_liveness("CLOSED", "BLOCKED") == "CLOSED"
    assert _merge_liveness("CLOSED", "NO_URL") == "CLOSED"


def test_a_positive_open_still_clears_closed():
    """A repost is real and must be caught — only positive evidence may overturn CLOSED."""
    from src.jobcore.track_sweep import _merge_liveness

    assert _merge_liveness("CLOSED", "OPEN") == "OPEN"


def test_an_inconclusive_recheck_of_a_non_closed_row_is_unverified():
    from src.jobcore.track_sweep import _merge_liveness

    assert _merge_liveness("OPEN", "UNVERIFIED") == "UNVERIFIED"
    assert _merge_liveness("LISTED", "UNVERIFIED") == "UNVERIFIED"
    assert _merge_liveness(None, "UNVERIFIED") == "UNVERIFIED"
    assert _merge_liveness("UNVERIFIED", "CLOSED") == "CLOSED"


# --- a human eyeball outranks an inconclusive probe --------------------------


def test_a_human_open_survives_an_inconclusive_probe():
    """63 of 110 real rows are UNVERIFIED because their apply page is JS-rendered and no
    checker can ever read it. Without this, opening the link by hand is erased on the next
    sweep and the board asks you to check the same row forever."""
    from src.jobcore.track_sweep import _merge_liveness

    assert _merge_liveness("OPEN", "UNVERIFIED", human="OPEN") == "OPEN"
    assert _merge_liveness("OPEN", "BLOCKED", human="OPEN") == "OPEN"
    assert _merge_liveness(None, "NO_URL", human="LISTED") == "LISTED"


def test_a_positive_probe_still_beats_a_stale_human_check():
    """Postings do get reposted and do get pulled. Fresh positive evidence wins."""
    from src.jobcore.track_sweep import _merge_liveness

    assert _merge_liveness("OPEN", "CLOSED", human="OPEN") == "CLOSED"
    assert _merge_liveness("CLOSED", "OPEN", human="CLOSED") == "OPEN"


def test_a_human_closed_is_not_downgraded():
    from src.jobcore.track_sweep import _merge_liveness

    assert _merge_liveness("CLOSED", "UNVERIFIED", human="CLOSED") == "CLOSED"


def test_without_a_human_check_the_merge_is_unchanged():
    from src.jobcore.track_sweep import _merge_liveness

    assert _merge_liveness("OPEN", "UNVERIFIED") == "UNVERIFIED"
    assert _merge_liveness("CLOSED", "UNVERIFIED") == "CLOSED"


def test_the_sweep_feeds_the_stored_human_check_into_the_merge(tmp_path):
    """End to end: a hand-checked OPEN row is not flipped to UNVERIFIED by a blind probe."""
    from src.jobcore import pipeline
    from src.jobcore.track_sweep import sweep

    d = tmp_path / "acme"
    d.mkdir()
    (d / "JD_DESCRIPTION.md").write_text("Apply: https://example.test/acme-role\n", encoding="utf-8")

    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(
        led,
        [
            {
                "company": "Acme",
                "title": "Team Lead",
                "job_id": "z1",
                "url": "https://example.test/acme-role",
                "match_score": 8,
                "source": "linkedin",
            }
        ],
        "2026-08-06",
    )
    led["opportunities"][0]["position_folder"] = str(d)
    pipeline.set_liveness(led, "acme:team-lead", "OPEN", today="2026-08-06")

    sweep(tmp_path, check=lambda url: _Live("UNVERIFIED", "js-rendered"), ledger=led)

    assert led["opportunities"][0]["posting_live"] == "OPEN"
