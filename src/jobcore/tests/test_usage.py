"""Tests for the session token/cost accounting in jobcore.usage."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from src.jobcore.usage import (
    CACHE_READ_MULTIPLIER,
    CACHE_WRITE_1H_MULTIPLIER,
    Usage,
    find_transcript,
    format_report,
    normalize_model,
    parse_transcript,
    project_slug,
    rates_for,
    usage_from_message,
)


def _assistant(usage: dict, model: str = "claude-opus-5") -> str:
    return json.dumps({"type": "assistant", "message": {"model": model, "usage": usage}})


def _user(text: str) -> str:
    return json.dumps({"type": "user", "message": {"content": [{"type": "text", "text": text}]}})


class TestModelNormalization:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("claude-opus-5", "claude-opus-5"),
            ("claude-opus-5[1m]", "claude-opus-5"),
            # `-fast` is NOT stripped: fast mode costs double and prices separately.
            ("claude-opus-4-8-fast", "claude-opus-4-8-fast"),
            ("claude-opus-5-fast[1m]", "claude-opus-5-fast"),
            ("  claude-sonnet-5  ", "claude-sonnet-5"),
            (None, ""),
        ],
    )
    def test_normalize(self, raw, expected):
        assert normalize_model(raw) == expected

    def test_context_suffix_still_prices(self):
        """A [1m] session must not fall through to 'unpriced'.

        The 1M context window carries no long-context premium on current models,
        so it bills at the standard rate.
        """
        assert rates_for("claude-opus-5[1m]") == (5.00, 25.00)

    def test_unknown_model_has_no_rates(self):
        assert rates_for("gpt-4") is None


class TestFastModePricing:
    """Regression: fast mode bills at 2x and must not be billed as standard.

    The old `normalize_model` stripped `-fast`, silently pricing fast-mode
    traffic at the standard rate — a 2x understatement that contradicted
    CLAUDE.md's own "$10/$50" note. A test asserting the stripping is what let
    the bug survive.
    """

    def test_fast_mode_is_double_standard(self):
        base = rates_for("claude-opus-5")
        fast = rates_for("claude-opus-5-fast")
        assert fast == (10.00, 50.00)
        assert fast == (base[0] * 2, base[1] * 2)

    def test_fast_mode_costs_twice_as_much_per_message(self):
        std = usage_from_message({"output_tokens": 1_000_000}, "claude-opus-5")
        fast = usage_from_message({"output_tokens": 1_000_000}, "claude-opus-5-fast")
        assert fast.cost_usd == pytest.approx(std.cost_usd * 2)

    def test_unknown_fast_variant_is_flagged_not_underbilled(self):
        """Better to warn 'unpriced' than to quietly bill at half rate."""
        u = usage_from_message({"output_tokens": 1000}, "claude-sonnet-4-6-fast")
        assert u.unpriced_messages == 1
        assert u.cost_usd == 0.0


class TestPromotionalPricing:
    """Sonnet 5 runs at an intro rate through 2026-08-31, then reverts."""

    def test_intro_rate_applies_inside_the_window(self):
        assert rates_for("claude-sonnet-5", on=date(2026, 7, 28)) == (2.00, 10.00)

    def test_standard_rate_applies_after_the_window(self):
        assert rates_for("claude-sonnet-5", on=date(2026, 9, 1)) == (3.00, 15.00)

    def test_boundary_day_is_still_intro(self):
        assert rates_for("claude-sonnet-5", on=date(2026, 8, 31)) == (2.00, 10.00)

    @staticmethod
    def _stamped(stamp: str) -> str:
        return json.dumps(
            {
                "type": "assistant",
                "timestamp": stamp,
                "message": {"model": "claude-sonnet-5", "usage": {"output_tokens": 1_000_000}},
            }
        )

    def test_transcript_spanning_cutoff_prices_each_side(self, tmp_path):
        """Each message bills against its OWN date, not today's."""
        p = tmp_path / "s.jsonl"
        p.write_text(
            "\n".join([self._stamped("2026-07-28T10:00:00.000Z"), self._stamped("2026-09-01T10:00:00.000Z")]),
            encoding="utf-8",
        )
        total, _ = parse_transcript(p)
        # 1M output at the intro $10 + 1M output at the standard $15
        assert total.cost_usd == pytest.approx(25.00)

    def test_unparseable_timestamp_falls_back_to_today(self):
        u = usage_from_message({"output_tokens": 1000}, "claude-sonnet-5", None)
        assert u.unpriced_messages == 0
        assert u.cost_usd > 0


class TestCostMath:
    def test_output_tokens_priced_at_output_rate(self):
        u = usage_from_message({"output_tokens": 1_000_000}, "claude-opus-5")
        assert u.cost_usd == pytest.approx(25.00)

    def test_input_tokens_priced_at_input_rate(self):
        u = usage_from_message({"input_tokens": 1_000_000}, "claude-opus-5")
        assert u.cost_usd == pytest.approx(5.00)

    def test_cache_read_is_discounted(self):
        u = usage_from_message({"cache_read_input_tokens": 1_000_000}, "claude-opus-5")
        assert u.cost_usd == pytest.approx(5.00 * CACHE_READ_MULTIPLIER)

    def test_one_hour_cache_write_costs_double_input(self):
        u = usage_from_message(
            {
                "cache_creation_input_tokens": 1_000_000,
                "cache_creation": {
                    "ephemeral_1h_input_tokens": 1_000_000,
                    "ephemeral_5m_input_tokens": 0,
                },
            },
            "claude-opus-5",
        )
        assert u.cache_write_1h_tokens == 1_000_000
        assert u.cost_usd == pytest.approx(5.00 * CACHE_WRITE_1H_MULTIPLIER)

    def test_missing_ttl_split_is_not_dropped(self):
        """Older transcripts lack cache_creation; the tokens must still bill."""
        u = usage_from_message({"cache_creation_input_tokens": 1000}, "claude-opus-5")
        assert u.cache_write_5m_tokens == 1000
        assert u.cost_usd > 0

    def test_unknown_model_is_flagged_not_silently_zeroed(self):
        u = usage_from_message({"output_tokens": 500}, "some-other-model")
        assert u.unpriced_messages == 1
        assert u.cost_usd == 0.0
        assert u.output_tokens == 500  # tokens still counted

    def test_total_tokens_sums_every_bucket(self):
        u = Usage(
            input_tokens=1,
            output_tokens=2,
            cache_read_tokens=4,
            cache_write_5m_tokens=8,
            cache_write_1h_tokens=16,
        )
        assert u.total_tokens == 31


class TestTranscriptParsing:
    def test_sums_across_messages(self, tmp_path):
        p = tmp_path / "s.jsonl"
        p.write_text(
            "\n".join(
                [
                    _assistant({"output_tokens": 100}),
                    _assistant({"output_tokens": 400}),
                ]
            ),
            encoding="utf-8",
        )
        total, _ = parse_transcript(p)
        assert total.output_tokens == 500
        assert total.messages == 2
        assert total.cost_usd == pytest.approx(500 * 25.00 / 1_000_000)

    def test_splits_by_user_turn(self, tmp_path):
        p = tmp_path / "s.jsonl"
        p.write_text(
            "\n".join(
                [
                    _user("first task"),
                    _assistant({"output_tokens": 100}),
                    _user("second task"),
                    _assistant({"output_tokens": 300}),
                    _assistant({"output_tokens": 100}),
                ]
            ),
            encoding="utf-8",
        )
        total, turns = parse_transcript(p)
        assert total.output_tokens == 500
        assert [label for label, _ in turns] == ["first task", "second task"]
        assert turns[0][1].output_tokens == 100
        assert turns[1][1].output_tokens == 400

    def test_tool_result_turns_do_not_start_a_new_segment(self, tmp_path):
        """A tool_result arrives as type=user but is not a real prompt."""
        p = tmp_path / "s.jsonl"
        p.write_text(
            "\n".join(
                [
                    _user("only real prompt"),
                    _assistant({"output_tokens": 100}),
                    json.dumps(
                        {
                            "type": "user",
                            "message": {"content": [{"type": "tool_result", "content": "ok"}]},
                        }
                    ),
                    _assistant({"output_tokens": 100}),
                ]
            ),
            encoding="utf-8",
        )
        _, turns = parse_transcript(p)
        assert len(turns) == 1
        assert turns[0][1].output_tokens == 200

    def test_system_reminder_only_turn_is_ignored(self, tmp_path):
        p = tmp_path / "s.jsonl"
        p.write_text(
            "\n".join(
                [
                    _user("real prompt"),
                    _assistant({"output_tokens": 50}),
                    _user("<system-reminder>background</system-reminder>"),
                    _assistant({"output_tokens": 50}),
                ]
            ),
            encoding="utf-8",
        )
        _, turns = parse_transcript(p)
        assert len(turns) == 1
        assert turns[0][1].output_tokens == 100

    def test_malformed_tail_line_does_not_crash(self, tmp_path):
        p = tmp_path / "s.jsonl"
        p.write_text(
            _assistant({"output_tokens": 10}) + '\n{"type": "assistant", "mess',
            encoding="utf-8",
        )
        total, _ = parse_transcript(p)
        assert total.output_tokens == 10

    def test_usage_before_any_user_turn_still_counts_in_total(self, tmp_path):
        p = tmp_path / "s.jsonl"
        p.write_text(_assistant({"output_tokens": 42}), encoding="utf-8")
        total, turns = parse_transcript(p)
        assert total.output_tokens == 42
        assert turns == []

    def test_server_tool_counts_are_aggregated(self, tmp_path):
        p = tmp_path / "s.jsonl"
        p.write_text(
            _assistant(
                {
                    "output_tokens": 1,
                    "server_tool_use": {
                        "web_search_requests": 2,
                        "web_fetch_requests": 3,
                    },
                }
            ),
            encoding="utf-8",
        )
        total, _ = parse_transcript(p)
        assert total.web_searches == 2
        assert total.web_fetches == 3


class TestTranscriptDiscovery:
    def test_slug_matches_claude_code_layout(self):
        assert (
            project_slug(r"C:\code1\test\.claude\worktrees\job-radar-linkedin-6")
            == "C--code1-test--claude-worktrees-job-radar-linkedin-6"
        )
        assert project_slug(r"C:\code1\test") == "C--code1-test"

    def test_returns_none_when_no_project_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        assert find_transcript(tmp_path / "nowhere") is None

    def test_picks_newest_transcript(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        cwd = tmp_path / "proj"
        base = tmp_path / ".claude" / "projects" / project_slug(cwd)
        base.mkdir(parents=True)
        old, new = base / "old.jsonl", base / "new.jsonl"
        old.write_text("", encoding="utf-8")
        new.write_text("", encoding="utf-8")
        os_utime_old, os_utime_new = (1_000_000, 2_000_000)
        import os as _os

        _os.utime(old, (os_utime_old, os_utime_old))
        _os.utime(new, (os_utime_new, os_utime_new))
        assert find_transcript(cwd) == new


class TestReport:
    def test_report_shows_cost_and_flags_unpriced(self):
        total = Usage(output_tokens=1000, messages=1, cost_usd=1.234)
        total.unpriced_messages = 2
        out = format_report(total, [], by_turn=False)
        assert "$1.23" in out
        assert "EXCLUDED" in out

    def test_by_turn_table_lists_each_prompt(self):
        turns = [("do a thing", Usage(output_tokens=10, cost_usd=0.5))]
        out = format_report(Usage(cost_usd=0.5), turns, by_turn=True)
        assert "By turn" in out
        assert "do a thing" in out


class TestMarginalCost:
    """The metric that makes the round-trip cost visible."""

    def test_marginal_cost_is_context_times_cache_read_rate(self):
        from src.jobcore.usage import marginal_cost_per_request

        u = Usage(cache_read_tokens=1_000_000, messages=10)
        u.models = {"claude-opus-5": 10}
        # 100k avg context * $5/M * 0.1
        assert marginal_cost_per_request(u) == pytest.approx(100_000 * 5.0 * 0.1 / 1_000_000)

    def test_none_when_no_messages(self):
        from src.jobcore.usage import marginal_cost_per_request

        assert marginal_cost_per_request(Usage()) is None

    def test_none_when_model_unpriced(self):
        from src.jobcore.usage import marginal_cost_per_request

        u = Usage(cache_read_tokens=1000, messages=1)
        u.models = {"mystery-model": 1}
        assert marginal_cost_per_request(u) is None

    def test_report_surfaces_the_round_trip_lever(self):
        u = Usage(cache_read_tokens=2_000_000, messages=10, cost_usd=1.0)
        u.models = {"claude-opus-5": 10}
        out = format_report(u, [], by_turn=False)
        assert "each additional tool call" in out


class TestContextGate:
    """Cost-calibrated port of claude-sdlc's Context Size Gate."""

    def _usage(self, cache_read: int, messages: int) -> Usage:
        u = Usage(cache_read_tokens=cache_read, messages=messages)
        u.models = {"claude-opus-5": messages}
        return u

    def test_small_context_is_ok(self):
        from src.jobcore.usage import GATE_OK, context_gate

        state, _ = context_gate(self._usage(100_000, 10))  # 10k ctx -> $0.005
        assert state == GATE_OK

    def test_mid_context_advises(self):
        from src.jobcore.usage import GATE_ADVISE, context_gate

        state, msg = context_gate(self._usage(2_500_000, 10))  # 250k -> $0.125
        assert state == GATE_ADVISE
        assert "compact" in msg.lower() or "subagent" in msg.lower()

    def test_large_context_says_stop(self):
        from src.jobcore.usage import GATE_STOP, context_gate

        state, msg = context_gate(self._usage(5_000_000, 10))  # 500k -> $0.25
        assert state == GATE_STOP
        assert "checkpoint" in msg.lower()

    def test_no_traffic_is_ok_not_a_crash(self):
        from src.jobcore.usage import GATE_OK, context_gate

        assert context_gate(Usage())[0] == GATE_OK

    def test_gate_exit_codes(self, tmp_path):
        """0=OK, 1=ADVISE, 2=STOP so a hook or script can branch on it."""
        from src.jobcore.usage import main

        p = tmp_path / "s.jsonl"
        # One message with a huge cached prefix -> STOP
        p.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "model": "claude-opus-5",
                        "usage": {"cache_read_input_tokens": 600_000, "output_tokens": 1},
                    },
                }
            ),
            encoding="utf-8",
        )
        assert main(["--transcript", str(p), "--gate"]) == 2

    def test_thresholds_are_ordered(self):
        from src.jobcore.usage import GATE_ADVISE_USD, GATE_STOP_USD

        assert 0 < GATE_ADVISE_USD < GATE_STOP_USD


class TestSubagentAccounting:
    """Regression: delegated spend used to be reported as exactly $0.00.

    Claude Code writes each subagent to `<session-id>/subagents/agent-*.jsonl`,
    not into the parent transcript, and sidechain records in the parent carry no
    `usage`. Summing only the parent made delegation look free — which is the one
    number you need to decide whether delegating was worth it.
    """

    def _session(self, tmp_path, *, sub_costs: list[int]) -> object:
        main_t = tmp_path / "sess.jsonl"
        main_t.write_text(_assistant({"output_tokens": 1_000_000}), encoding="utf-8")
        if sub_costs:
            d = tmp_path / "sess" / "subagents"
            d.mkdir(parents=True)
            for i, out in enumerate(sub_costs):
                (d / f"agent-{i}.jsonl").write_text(
                    _assistant({"output_tokens": out}, model="claude-haiku-4-5"), encoding="utf-8"
                )
        return main_t

    def test_finds_subagent_transcripts(self, tmp_path):
        from src.jobcore.usage import find_subagent_transcripts

        p = self._session(tmp_path, sub_costs=[1000, 2000])
        assert len(find_subagent_transcripts(p)) == 2

    def test_no_subagent_dir_is_not_an_error(self, tmp_path):
        from src.jobcore.usage import find_subagent_transcripts, parse_subagents

        p = self._session(tmp_path, sub_costs=[])
        assert find_subagent_transcripts(p) == []
        usage, count = parse_subagents(p)
        assert (usage.cost_usd, count) == (0.0, 0)

    def test_subagent_spend_is_summed(self, tmp_path):
        from src.jobcore.usage import parse_subagents

        p = self._session(tmp_path, sub_costs=[1_000_000, 1_000_000])
        usage, count = parse_subagents(p)
        assert count == 2
        # 2M output tokens on haiku at $5/M
        assert usage.cost_usd == pytest.approx(10.00)

    def test_subagent_cost_is_not_silently_zero(self, tmp_path):
        """The exact defect: a session WITH subagents must not report $0 for them."""
        from src.jobcore.usage import parse_subagents

        usage, _ = parse_subagents(self._session(tmp_path, sub_costs=[500_000]))
        assert usage.cost_usd > 0

    def test_report_shows_delegated_spend_separately(self, tmp_path):
        from src.jobcore.usage import parse_subagents, parse_transcript

        p = self._session(tmp_path, sub_costs=[1_000_000])
        total, turns = parse_transcript(p)
        subs, count = parse_subagents(p)
        out = format_report(total, turns, False, subs, count)
        assert "Delegated to subagents" in out
        assert "main $" in out  # combined headline splits main vs subagents

    def test_gate_counts_delegated_dollars(self):
        from src.jobcore.usage import GATE_ADVISE, GATE_OK, context_gate

        main_u = Usage(cache_read_tokens=100_000, messages=10, cost_usd=1.0)
        main_u.models = {"claude-opus-5": 10}
        assert context_gate(main_u)[0] == GATE_OK
        # Delegated spend pushes total over the advise threshold
        subs = Usage(cost_usd=15.0, messages=5)
        assert context_gate(main_u, subs)[0] == GATE_ADVISE


class TestSpendArmOfGate:
    """The marginal-cost arm is blind to one-shot ingestion; spend arm catches it.

    Measured 2026-07-28: a real session reported a healthy $0.030/round trip
    while a single skill load drove it to $15.85 total, 73% cache-writes. The
    old gate said OK the whole way.
    """

    def _ingestion_heavy(self) -> Usage:
        # Tiny context re-read (cheap marginal) but a huge 1h cache write.
        u = Usage(cache_read_tokens=100_000, messages=10, cache_write_1h_tokens=1_500_000)
        u.models = {"claude-opus-5": 10}
        u.cost_usd = (100_000 * 0.1 + 1_500_000 * 2.0) * 5.0 / 1_000_000
        return u

    def test_marginal_arm_alone_would_say_ok(self):
        from src.jobcore.usage import GATE_ADVISE_USD, marginal_cost_per_request

        assert marginal_cost_per_request(self._ingestion_heavy()) < GATE_ADVISE_USD

    def test_spend_arm_escalates_anyway(self):
        from src.jobcore.usage import GATE_OK, context_gate

        state, msg = context_gate(self._ingestion_heavy())
        assert state != GATE_OK
        assert "spent" in msg

    def test_ingestion_share_is_surfaced(self):
        from src.jobcore.usage import context_gate, ingestion_share

        u = self._ingestion_heavy()
        assert ingestion_share(u) > 0.9
        assert "cache-writes" in context_gate(u)[1]

    def test_cost_shares_sum_to_one(self):
        from src.jobcore.usage import cost_shares

        u = Usage(
            cache_read_tokens=1_000_000,
            cache_write_1h_tokens=200_000,
            output_tokens=50_000,
            messages=10,
        )
        u.models = {"claude-opus-5": 10}
        u.cost_usd = (1_000_000 * 0.1 + 200_000 * 2.0) * 5.0 / 1_000_000 + 50_000 * 25.0 / 1_000_000
        assert sum(cost_shares(u)) == pytest.approx(1.0)

    def test_cheap_session_stays_ok(self):
        from src.jobcore.usage import GATE_OK, context_gate

        u = Usage(cache_read_tokens=100_000, messages=10, cost_usd=0.5)
        u.models = {"claude-opus-5": 10}
        assert context_gate(u)[0] == GATE_OK

    def test_spend_thresholds_are_ordered(self):
        from src.jobcore.usage import GATE_ADVISE_SPEND_USD, GATE_STOP_SPEND_USD

        assert 0 < GATE_ADVISE_SPEND_USD < GATE_STOP_SPEND_USD


class TestCliEncodesOnLegacyConsoles:
    """Regression: the report must survive a cp1252 stdout (Windows default).

    Every other report test asserts on the string ``format_report`` *returns*,
    which never crosses the encode boundary — so the whole suite stayed green
    while ``uv run python -m src.jobcore.usage`` died with UnicodeEncodeError on
    the 🟢 gate icon. These run the CLI as a subprocess with a legacy codepage,
    which is the only shape that actually exercises the ``print``.
    """

    def _transcript(self, tmp_path):
        p = tmp_path / "s.jsonl"
        p.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "model": "claude-opus-5",
                        "usage": {"cache_read_input_tokens": 120_000, "output_tokens": 10},
                    },
                }
            ),
            encoding="utf-8",
        )
        return p

    def _run(self, tmp_path, *args):
        import os
        import subprocess
        import sys

        env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
        return subprocess.run(
            [sys.executable, "-m", "src.jobcore.usage", "--transcript", str(self._transcript(tmp_path)), *args],
            capture_output=True,
            text=True,
            encoding="cp1252",
            env=env,
            cwd=Path(__file__).resolve().parents[3],
        )

    def test_default_report_does_not_crash(self, tmp_path):
        r = self._run(tmp_path)
        assert r.returncode == 0, r.stderr
        assert "UnicodeEncodeError" not in r.stderr
        assert "Session cost" in r.stdout

    def test_gate_does_not_crash(self, tmp_path):
        r = self._run(tmp_path, "--gate")
        assert "UnicodeEncodeError" not in r.stderr
        assert r.returncode in (0, 1, 2), r.stderr


class TestLongThreadDetection:
    """Build trigger from claude-sdlc ADR-0001: flag a thread with 20+ sequential
    API turns and no subagent boundary — the point where schema-patched state
    would start to outperform conversational accumulation.
    """

    def test_short_autonomous_stretch_is_below_threshold(self, tmp_path):
        from src.jobcore.usage import LONG_THREAD_THRESHOLD, longest_single_thread

        p = tmp_path / "s.jsonl"
        p.write_text(
            "\n".join([_user("go"), *[_assistant({"output_tokens": 10}) for _ in range(5)]]),
            encoding="utf-8",
        )
        label, count = longest_single_thread(p)
        assert label == "main session (one turn)"
        assert count == 5 < LONG_THREAD_THRESHOLD

    def test_long_autonomous_stretch_within_one_turn_is_flagged(self, tmp_path):
        from src.jobcore.usage import LONG_THREAD_THRESHOLD, longest_single_thread

        p = tmp_path / "s.jsonl"
        p.write_text(
            "\n".join([_user("go"), *[_assistant({"output_tokens": 10}) for _ in range(25)]]),
            encoding="utf-8",
        )
        label, count = longest_single_thread(p)
        assert label == "main session (one turn)"
        assert count == 25 >= LONG_THREAD_THRESHOLD

    def test_many_short_human_paced_turns_do_not_trip_the_flag(self, tmp_path):
        """A long interactive session (many human turns, each short) is a
        different, already-mitigated shape (/compact, /checkpoint) — not the
        unbroken-autonomous-run shape this trigger targets. The whole-session
        total can be large while every individual turn stays short."""
        from src.jobcore.usage import LONG_THREAD_THRESHOLD, longest_single_thread

        lines = []
        for _ in range(30):  # 30 human turns x 3 assistant steps = 90 total messages
            lines.append(_user("next"))
            lines += [_assistant({"output_tokens": 10}) for _ in range(3)]
        p = tmp_path / "s.jsonl"
        p.write_text("\n".join(lines), encoding="utf-8")
        _, count = longest_single_thread(p)
        assert count == 3 < LONG_THREAD_THRESHOLD

    def test_a_long_subagent_thread_is_found_even_when_main_is_short(self, tmp_path):
        """A single subagent can run long even when the parent thread stays short —
        each transcript is its own continuous context and must be checked on its
        own, not folded into the parent's total."""
        from src.jobcore.usage import longest_single_thread

        main_t = tmp_path / "sess.jsonl"
        main_t.write_text(_assistant({"output_tokens": 10}), encoding="utf-8")
        sub_dir = tmp_path / "sess" / "subagents"
        sub_dir.mkdir(parents=True)
        (sub_dir / "agent-0.jsonl").write_text(
            "\n".join(_assistant({"output_tokens": 10}, model="claude-haiku-4-5") for _ in range(30)),
            encoding="utf-8",
        )
        label, count = longest_single_thread(main_t)
        assert label == "agent-0"
        assert count == 30

    def test_report_shows_the_flag_only_past_threshold(self, tmp_path):
        p = tmp_path / "s.jsonl"
        p.write_text("\n".join(_assistant({"output_tokens": 10}) for _ in range(25)), encoding="utf-8")
        total, turns = parse_transcript(p)
        assert "LONG SINGLE THREAD" in format_report(total, turns, False, long_thread=("main session", 25))
        assert "LONG SINGLE THREAD" not in format_report(total, turns, False, long_thread=("main session", 5))
        assert "LONG SINGLE THREAD" not in format_report(total, turns, False, long_thread=None)
