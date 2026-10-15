"""The CSV lane must not be hardcoded to one job board.

`CSV_PATTERN = "Referally Leadership*.csv"` and an inline `"Referally" not in sender` check
meant a second user pointing this at their own export got **zero rows and no error** — the
worst failure shape, because an empty shortlist reads as "quiet market", not "misconfigured".
"""

from __future__ import annotations

import json

from ..csv_ingest import _detect_format, _sender_ok, csv_pattern, find_latest_csv, sender_filter


def _write_profile(tmp_path, monkeypatch, csv_cfg: dict) -> None:
    prof = tmp_path / "prof"
    prof.mkdir(exist_ok=True)
    (prof / "profile.json").write_text(
        json.dumps({"schema_version": "1.0", "identity": {"name": "X"}, "csv": csv_cfg}),
        encoding="utf-8",
    )
    monkeypatch.setenv("PROFILE_DIR", str(prof))


class TestCsvPatternComesFromTheProfile:
    def test_profile_pattern_is_used(self, tmp_path, monkeypatch) -> None:
        _write_profile(tmp_path, monkeypatch, {"pattern": "my-board*.csv"})
        assert csv_pattern() == "my-board*.csv"

    def test_absent_config_does_not_fall_back_to_one_boards_filename(self, tmp_path, monkeypatch) -> None:
        _write_profile(tmp_path, monkeypatch, {})
        assert "Referally" not in csv_pattern()

    def test_find_latest_csv_honours_an_injected_pattern(self, tmp_path) -> None:
        assert find_latest_csv(pattern="definitely-no-such-file-*.csv") is None


class TestSenderFilter:
    spec = _detect_format(["Message", "Display Name", "Time"])

    def test_empty_filter_keeps_every_row(self) -> None:
        assert _sender_ok({"Display Name": "Anyone At All"}, self.spec, wanted="")

    def test_matching_sender_is_kept_case_insensitively(self) -> None:
        assert _sender_ok({"Display Name": "jobsbot daily"}, self.spec, wanted="JobsBot")

    def test_non_matching_sender_is_dropped(self) -> None:
        assert not _sender_ok({"Display Name": "Aunt Mildred"}, self.spec, wanted="JobsBot")

    def test_filter_is_read_from_the_profile(self, tmp_path, monkeypatch) -> None:
        _write_profile(tmp_path, monkeypatch, {"pattern": "*.csv", "sender_filter": "MyBoard"})
        assert sender_filter() == "MyBoard"
        assert _sender_ok({"Display Name": "MyBoard Digest"}, self.spec)
        assert not _sender_ok({"Display Name": "Referally"}, self.spec)
