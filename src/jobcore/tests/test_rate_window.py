"""Tests for src.jobcore.rate_window."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.jobcore.rate_window import (
    GATE_PAUSE,
    GATE_RUN,
    _mtime_at_or_after,
    all_local_transcripts,
    calibration_ceiling,
    estimated_pct,
    find_hard_stops,
    pause_gate,
    rolling_window_usage,
)


def _assistant_at(stamp: str, usage: dict, model: str = "claude-sonnet-5") -> str:
    return json.dumps({"type": "assistant", "timestamp": stamp, "message": {"model": model, "usage": usage}})


def _user_hit_limit_at(stamp: str, local_time: str, tz: str) -> str:
    text = f"You've hit your session limit · resets {local_time} ({tz})"
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": stamp,
            "message": {"content": [{"type": "text", "text": text}]},
        }
    )


def _write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestMtimeAtOrAfter:
    """The file-level skip that keeps a scan of hundreds of real transcripts fast."""

    def test_recent_file_passes(self, tmp_path: Path) -> None:
        p = tmp_path / "recent.jsonl"
        p.write_text("{}", encoding="utf-8")
        cutoff = datetime.now(UTC) - timedelta(hours=1)
        assert _mtime_at_or_after(p, cutoff) is True

    def test_old_file_is_excluded(self, tmp_path: Path) -> None:
        p = tmp_path / "old.jsonl"
        p.write_text("{}", encoding="utf-8")
        old_epoch = (datetime.now(UTC) - timedelta(days=30)).timestamp()
        os.utime(p, (old_epoch, old_epoch))
        cutoff = datetime.now(UTC) - timedelta(hours=1)
        assert _mtime_at_or_after(p, cutoff) is False

    def test_missing_file_fails_toward_reading_it(self, tmp_path: Path) -> None:
        cutoff = datetime.now(UTC) - timedelta(hours=1)
        assert _mtime_at_or_after(tmp_path / "does-not-exist.jsonl", cutoff) is True


class TestAllLocalTranscripts:
    def test_finds_transcripts_across_multiple_project_dirs(self, tmp_path: Path) -> None:
        base = tmp_path / "projects"
        _write(base / "repo-a" / "s1.jsonl", ["{}"])
        _write(base / "repo-b" / "s2.jsonl", ["{}"])
        found = all_local_transcripts(base=base)
        assert {p.name for p in found} == {"s1.jsonl", "s2.jsonl"}

    def test_missing_base_returns_empty(self, tmp_path: Path) -> None:
        assert all_local_transcripts(base=tmp_path / "does-not-exist") == []


class TestRollingWindowUsage:
    def test_sums_only_records_inside_the_window(self, tmp_path: Path) -> None:
        base = tmp_path / "projects"
        now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
        inside = (now - timedelta(hours=2)).isoformat()
        outside = (now - timedelta(hours=6)).isoformat()
        _write(
            base / "repo-a" / "s1.jsonl",
            [
                _assistant_at(inside, {"input_tokens": 1_000_000}),
                _assistant_at(outside, {"input_tokens": 1_000_000}),
            ],
        )
        total = rolling_window_usage(now, window_hours=5.0, base=base)
        # Only the "inside" record's 1M input tokens counts, at sonnet-5's active promotional
        # rate through 2026-08-31 ($2.00/M input, per usage.py's INTRO_PRICING).
        assert total.cost_usd == 2.00

    def test_sums_across_multiple_projects_and_subagents(self, tmp_path: Path) -> None:
        base = tmp_path / "projects"
        now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
        inside = (now - timedelta(hours=1)).isoformat()
        _write(base / "repo-a" / "s1.jsonl", [_assistant_at(inside, {"input_tokens": 1_000_000})])
        _write(
            base / "repo-b" / "s2" / "subagents" / "agent-a1.jsonl",
            [_assistant_at(inside, {"input_tokens": 1_000_000})],
        )
        _write(base / "repo-b" / "s2.jsonl", ["{}"])
        total = rolling_window_usage(now, window_hours=5.0, base=base)
        assert total.cost_usd == 4.00  # both count, main + subagent, at the $2.00/M intro rate

    def test_empty_when_nothing_local(self, tmp_path: Path) -> None:
        now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
        total = rolling_window_usage(now, window_hours=5.0, base=tmp_path / "projects")
        assert total.cost_usd == 0.0
        assert total.messages == 0


class TestFindHardStops:
    def test_parses_the_real_logged_format(self, tmp_path: Path) -> None:
        base = tmp_path / "projects"
        # Seen at 09:35 UTC = 12:35 Asia/Jerusalem (IDT, UTC+3 in August); resets 14:30 same day.
        seen = "2026-08-06T09:35:00+00:00"
        _write(base / "repo-a" / "s1.jsonl", [_user_hit_limit_at(seen, "2:30pm", "Asia/Jerusalem")])
        stops = find_hard_stops(base=base)
        assert len(stops) == 1
        stop = stops[0]
        assert stop.seen_at == datetime.fromisoformat(seen)
        # 14:30 Asia/Jerusalem == 11:30 UTC that day.
        assert stop.reset_at == datetime(2026, 8, 6, 11, 30, tzinfo=UTC)

    def test_handles_rollover_past_midnight_local(self, tmp_path: Path) -> None:
        base = tmp_path / "projects"
        # Seen at 19:00 UTC = 22:00 Asia/Jerusalem; resets 12:30am local -> next calendar day.
        seen = "2026-08-06T19:00:00+00:00"
        _write(base / "repo-a" / "s1.jsonl", [_user_hit_limit_at(seen, "12:30am", "Asia/Jerusalem")])
        stops = find_hard_stops(base=base)
        # 2026-08-07 00:30 Asia/Jerusalem == 2026-08-06 21:30 UTC.
        assert stops[0].reset_at == datetime(2026, 8, 6, 21, 30, tzinfo=UTC)

    def test_no_match_returns_empty(self, tmp_path: Path) -> None:
        base = tmp_path / "projects"
        record = json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-08-06T09:00:00+00:00",
                "message": {"content": [{"type": "text", "text": "all good"}]},
            }
        )
        _write(base / "repo-a" / "s1.jsonl", [record])
        assert find_hard_stops(base=base) == []

    def test_respects_the_lookback_bound(self, tmp_path: Path) -> None:
        """A hard-stop in a file untouched in a long time is out of scope - old history
        isn't useful calibration data, and scanning it is what made this slow in practice."""
        base = tmp_path / "projects"
        seen = "2026-08-06T09:35:00+00:00"
        path = base / "repo-a" / "s1.jsonl"
        _write(path, [_user_hit_limit_at(seen, "2:30pm", "Asia/Jerusalem")])
        now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
        old_epoch = (now - timedelta(days=60)).timestamp()
        os.utime(path, (old_epoch, old_epoch))
        assert find_hard_stops(base=base, now=now, lookback_days=30.0) == []
        # Widening the lookback past the file's real mtime finds it again.
        assert len(find_hard_stops(base=base, now=now, lookback_days=90.0)) == 1


class TestCalibrationCeiling:
    def test_none_when_no_hard_stop_ever_seen(self, tmp_path: Path) -> None:
        base = tmp_path / "projects"
        _write(base / "repo-a" / "s1.jsonl", ["{}"])
        assert calibration_ceiling(datetime.now(UTC), base=base) is None

    def test_averages_spend_preceding_each_real_hit(self, tmp_path: Path) -> None:
        base = tmp_path / "projects"
        seen = datetime(2026, 8, 6, 9, 35, tzinfo=UTC)
        # One priced message 1h before the hit (inside its own 5h window), plus the hit itself.
        before = (seen - timedelta(hours=1)).isoformat()
        lines = [
            _assistant_at(before, {"input_tokens": 1_000_000}),  # $2.00 at sonnet-5's intro rate
            _user_hit_limit_at(seen.isoformat(), "2:30pm", "Asia/Jerusalem"),
        ]
        _write(base / "repo-a" / "s1.jsonl", lines)
        ceiling = calibration_ceiling(seen + timedelta(hours=1), base=base)
        assert ceiling == 2.00

    def test_caps_to_the_most_recent_n_samples(self, tmp_path: Path) -> None:
        """7 real stops on this machine at once made calibration_ceiling call
        rolling_window_usage 7 times - most of a real ~52s pause_gate() call. Only the most
        recent MAX_CALIBRATION_SAMPLES should be averaged."""
        base = tmp_path / "projects"
        first_seen = datetime(2026, 1, 1, 9, 35, tzinfo=UTC)
        for i in range(7):  # oldest -> newest, $1.00 .. $7.00, 2 days apart (windows never overlap)
            seen = first_seen + timedelta(days=2 * i)
            before = (seen - timedelta(hours=1)).isoformat()
            dollars = i + 1
            lines = [
                _assistant_at(before, {"input_tokens": dollars * 500_000}),  # $dollars at $2/M
                _user_hit_limit_at(seen.isoformat(), "2:30pm", "Asia/Jerusalem"),
            ]
            _write(base / f"repo-{i}" / "s.jsonl", lines)
        now = first_seen + timedelta(days=2 * 6, hours=1)
        ceiling = calibration_ceiling(now, base=base)
        # Most recent 5 of [1..7] are [3,4,5,6,7] -> average 5.0, not the full-set average of 4.0.
        assert ceiling == 5.0


class TestEstimatedPct:
    def test_none_without_a_ceiling(self, tmp_path: Path) -> None:
        base = tmp_path / "projects"
        _write(base / "repo-a" / "s1.jsonl", ["{}"])
        assert estimated_pct(datetime.now(UTC), base=base) is None

    def test_clamped_to_100(self, tmp_path: Path) -> None:
        base = tmp_path / "projects"
        seen = datetime(2026, 8, 6, 9, 35, tzinfo=UTC)
        before = (seen - timedelta(hours=1)).isoformat()
        now = seen + timedelta(hours=1)
        lines = [
            _assistant_at(before, {"input_tokens": 1_000_000}),  # ceiling sample = $2.00
            _user_hit_limit_at(seen.isoformat(), "2:30pm", "Asia/Jerusalem"),
            _assistant_at(now.isoformat(), {"input_tokens": 3_000_000}),  # way over the ceiling
        ]
        _write(base / "repo-a" / "s1.jsonl", lines)
        assert estimated_pct(now, base=base) == 100.0


class TestPauseGate:
    def test_runs_when_no_data_at_all(self, tmp_path: Path) -> None:
        base = tmp_path / "projects"
        _write(base / "repo-a" / "s1.jsonl", ["{}"])
        result = pause_gate(datetime.now(UTC), base=base)
        assert result.state == GATE_RUN
        assert result.reset_at is None

    def test_pauses_on_a_real_hard_stop_still_in_effect(self, tmp_path: Path) -> None:
        base = tmp_path / "projects"
        now = datetime(2026, 8, 6, 10, 0, tzinfo=UTC)
        seen = (now - timedelta(minutes=30)).isoformat()
        _write(base / "repo-a" / "s1.jsonl", [_user_hit_limit_at(seen, "2:30pm", "Asia/Jerusalem")])
        result = pause_gate(now, base=base)
        assert result.state == GATE_PAUSE
        assert result.exact is True
        assert result.reset_at == datetime(2026, 8, 6, 11, 30, tzinfo=UTC)

    def test_runs_once_the_hard_stop_reset_time_has_passed(self, tmp_path: Path) -> None:
        base = tmp_path / "projects"
        seen = "2026-08-06T09:35:00+00:00"
        _write(base / "repo-a" / "s1.jsonl", [_user_hit_limit_at(seen, "2:30pm", "Asia/Jerusalem")])
        after_reset = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)  # past the 11:30 UTC reset
        result = pause_gate(after_reset, base=base)
        assert result.state == GATE_RUN

    def test_pauses_on_estimate_crossing_85_percent(self, tmp_path: Path) -> None:
        base = tmp_path / "projects"
        seen = datetime(2026, 8, 6, 9, 35, tzinfo=UTC)
        before = (seen - timedelta(hours=1)).isoformat()
        now = datetime(2026, 8, 6, 20, 0, tzinfo=UTC)  # long past that reset
        lines = [
            _assistant_at(before, {"input_tokens": 1_000_000}),  # ceiling sample
            _user_hit_limit_at(seen.isoformat(), "2:30pm", "Asia/Jerusalem"),
            _assistant_at((now - timedelta(hours=1)).isoformat(), {"input_tokens": 950_000}),  # 95% of ceiling
        ]
        _write(base / "repo-a" / "s1.jsonl", lines)
        result = pause_gate(now, base=base)
        assert result.state == GATE_PAUSE
        assert result.exact is False
        assert result.reset_at == now + timedelta(hours=5)
