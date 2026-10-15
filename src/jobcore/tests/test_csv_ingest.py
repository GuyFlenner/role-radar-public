import csv
from datetime import date, timedelta

from src.jobcore.csv_ingest import filter_score_positions, job_key, load_new_positions


def _body(title, company, location="Tel Aviv", domain="Software", url="https://x.co/1"):
    return f"*{title}* / {company}\n_{location}_ | _{domain}_\n{url}"


def _write_legacy(path, rows):
    """rows = list of (body, iso_date). Legacy semicolon format."""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["MessageBody", "UserName", "Date1", "Date2"])
        for body, d in rows:
            w.writerow([body, "Referally Bot", "", d])


def _write_whatsapp(path, rows):
    """rows = list of (body, 'M/D/YYYY'). WhatsApp comma format."""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=",")
        w.writerow(["Message", "Display Name", "Time"])
        for body, d in rows:
            w.writerow([body, "Referally Bot", f"{d}, 2:00:00 PM"])


def _recent():
    return (date.today() - timedelta(days=5)).strftime("%Y-%m-%d")


def _recent_us():
    """Same as _recent() but in the WhatsApp export's M/D/YYYY form (no zero padding)."""
    d = date.today() - timedelta(days=5)
    return f"{d.month}/{d.day}/{d.year}"


def _stale():
    return (date.today() - timedelta(days=200)).strftime("%Y-%m-%d")


def test_legacy_format_parses(tmp_path):
    csvp = tmp_path / "Referally Leadership legacy.csv"
    _write_legacy(csvp, [(_body("Backend Team Lead", "Acme"), _recent())])
    jobs, _ = load_new_positions(csvp, delta_only=False)
    assert len(jobs) == 1
    assert jobs[0]["company"] == "Acme"
    assert jobs[0]["title"] == "Backend Team Lead"


def test_whatsapp_format_parses(tmp_path):
    csvp = tmp_path / "Referally Leadership wa.csv"
    _write_whatsapp(csvp, [(_body("AI Team Lead", "Globex"), _recent_us())])
    jobs, _ = load_new_positions(csvp, delta_only=False)
    assert len(jobs) == 1 and jobs[0]["company"] == "Globex"


def test_stale_dropped(tmp_path):
    csvp = tmp_path / "Referally Leadership stale.csv"
    _write_legacy(
        csvp,
        [
            (_body("Backend Team Lead", "FreshCo"), _recent()),
            (_body("Engineering Manager", "OldCo"), _stale()),
        ],
    )
    jobs, stale = load_new_positions(csvp, delta_only=False)
    companies = {j["company"] for j in jobs}
    assert "FreshCo" in companies and "OldCo" not in companies
    assert stale == 1


def test_reposts_deduped(tmp_path):
    csvp = tmp_path / "Referally Leadership dup.csv"
    _write_legacy(
        csvp,
        [
            (_body("Backend Team Lead", "Acme"), _recent()),
            (_body("Backend Team Lead", "Acme"), _recent()),
        ],
    )
    jobs, _ = load_new_positions(csvp, delta_only=False)
    assert len(jobs) == 1


def test_sorted_by_score_desc(tmp_path):
    csvp = tmp_path / "Referally Leadership rank.csv"
    _write_legacy(
        csvp,
        [
            (_body("Software Team Lead", "PlainCo", domain="Software"), _recent()),
            (_body("LLM Team Lead", "AICo", domain="Software"), _recent()),
        ],
    )
    jobs, _ = load_new_positions(csvp, delta_only=False)
    assert [j["match_score"] for j in jobs] == sorted((j["match_score"] for j in jobs), reverse=True)
    assert jobs[0]["match_score"] >= 8  # AI-leadership floored high


# --- delta / seen-store keying (regression) ----------------------------------


def test_delta_suppresses_ats_job_on_second_run_via_job_id():
    """Regression: output rows dropped ``job_id``, so ``save_seen`` keyed ("tc", ...)
    while the delta comparison keyed ("id", ...) — the two key spaces never matched
    and every ATS/LinkedIn role re-surfaced as new on every run."""
    raw = [
        {
            "title": "Backend Team Lead",
            "company": "Cato Networks",
            "location": "Tel Aviv",
            "url": "https://example.test/1",
            "job_id": "12345",
            "source": "greenhouse",
        }
    ]
    first, _ = filter_score_positions(raw, prev_keys=set(), require_preferred_location=False)
    assert len(first) == 1
    assert first[0]["job_id"] == "12345", "output row must carry job_id or the seen-store drifts"

    prev = {job_key(row) for row in first}
    assert prev == {("id", "12345")}, f"seen-store must key on job_id, got {prev}"

    second, _ = filter_score_positions(raw, prev_keys=prev, require_preferred_location=False)
    assert second == [], "a previously-seen ATS role must be suppressed on the next run"


def test_delta_still_keys_csv_rows_on_title_company_when_no_job_id():
    """Back-compat: the Referally CSV path has no job_id, so it must keep the ("tc", ...) key."""
    raw = [{"title": "Backend Team Lead", "company": "Acme", "location": "Tel Aviv"}]
    rows, _ = filter_score_positions(raw, prev_keys=set(), require_preferred_location=False)
    assert job_key(rows[0]) == ("tc", "backend team lead", "acme")
