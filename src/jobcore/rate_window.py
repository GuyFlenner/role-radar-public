"""Account-wide rolling-window usage estimate and hard-stop detection.

Claude Code's Pro/Max plans meter usage against a rolling 5-hour window shared
across every local session and subagent. Anthropic exposes no API for "percent
of window used"; the only guaranteed signal is the literal error message it
writes into a transcript when the window is actually exhausted:
``"You've hit your session limit · resets HH:MMam/pm (Timezone)"``. Everything
here is a local, deterministic, zero-LLM-call estimate built from that plus
the existing token/cost accounting in :mod:`src.jobcore.usage` — see
``docs/superpowers/specs/2026-08-13-rate-limit-pause-resume-design.md``.

CLI:
    uv run python -m src.jobcore.rate_window --gate
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from src.jobcore.console import force_utf8_output
from src.jobcore.usage import (
    Usage,
    find_subagent_transcripts,
    usage_from_message,
)

#: Default lookback for find_hard_stops - bounds a scan of hundreds of local transcripts to
#: only recently-touched files. A hard-stop older than this is unlikely to still be a useful
#: calibration sample anyway (Anthropic's own quota mechanics can change), so this is a real
#: scope limit, not an approximation of one.
DEFAULT_LOOKBACK_DAYS = 30.0


def all_local_transcripts(base: Path | None = None) -> list[Path]:
    """Every main-session transcript across every local project dir.

    The usage window is account-wide, not per-repo, so a rolling estimate must
    look at every project Claude Code has written a transcript for on this
    machine, not just the current one.
    """
    base = base or (Path.home() / ".claude" / "projects")
    if not base.is_dir():
        return []
    return sorted(p for p in base.glob("*/*.jsonl") if p.is_file())


def _usage_in_window(path: Path, window_start: datetime, window_end: datetime) -> Usage:
    """Sum usage from records in ``path`` whose own timestamp falls in the window.

    Measured live (cProfile against 704 real transcripts): decoding every line as JSON
    accounted for ~48s of a ~52s call, even though only a small fraction of lines carry a
    ``usage`` field at all (most are tool_use/tool_result/user turns). A raw substring check
    before ``json.loads`` is nearly free and skips the decode entirely for every line that
    cannot possibly match.
    """
    total = Usage()
    try:
        fh = open(path, encoding="utf-8")
    except OSError:
        return total
    with fh:
        for line in fh:
            if '"usage"' not in line:
                continue
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            stamp = record.get("timestamp")
            if not isinstance(stamp, str) or not stamp:
                continue
            try:
                ts = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError:
                continue
            if ts.tzinfo is None:
                continue
            if not (window_start <= ts <= window_end):
                continue
            message = record.get("message") or {}
            raw = message.get("usage")
            if not isinstance(raw, dict):
                continue
            total.add(usage_from_message(raw, message.get("model"), ts.date()))
    return total


def _mtime_at_or_after(path: Path, cutoff: datetime) -> bool:
    """True if ``path``'s last-modified time is at/after ``cutoff``, or if it can't be statted.

    Transcripts are append-only, so a file whose last write predates the window start cannot
    contain any record inside it — skipping the open+parse entirely is what keeps a scan of
    hundreds of MB of local history fast. Never silently skip on a stat error: fail toward
    reading the file, not toward dropping real data.
    """
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=cutoff.tzinfo)
    except OSError:
        return True
    return mtime >= cutoff


def rolling_window_usage(now: datetime, window_hours: float = 5.0, base: Path | None = None) -> Usage:
    """Sum ``$``-equivalent spend across every local transcript in the trailing window.

    Includes subagent transcripts (real spend that never enters a main context,
    per ``usage.py``'s own delegation-cost lesson). Skips any file whose mtime predates the
    window entirely (see :func:`_mtime_at_or_after`) — measured live against 704 real local
    transcripts (~600MB incl. subagents): unfiltered, one call took minutes; this makes it
    proportional to how much actually happened in the last ``window_hours``, not to this
    machine's whole Claude Code history.
    """
    window_start = now - timedelta(hours=window_hours)
    total = Usage()
    for path in all_local_transcripts(base=base):
        if _mtime_at_or_after(path, window_start):
            total.add(_usage_in_window(path, window_start, now))
        for sub_path in find_subagent_transcripts(path):
            if _mtime_at_or_after(sub_path, window_start):
                total.add(_usage_in_window(sub_path, window_start, now))
    return total


_HARD_STOP_RE = re.compile(
    r"hit your session limit.{0,10}resets\s+(\d{1,2}:\d{2}\s*(?:am|pm))\s*\(([^)]+)\)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class HardStop:
    """One real, observed rate-limit hit."""

    seen_at: datetime  # when the message was written (UTC, from the transcript record)
    reset_at: datetime  # the parsed reset time, resolved to an absolute UTC instant
    source: Path


def _message_text(record: dict) -> str:
    message = record.get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _resolve_reset_at(seen_at: datetime, local_time: str, tz_name: str) -> datetime | None:
    """Resolve 'HH:MMam/pm' in ``tz_name`` to an absolute UTC instant near ``seen_at``."""
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        return None
    cleaned = local_time.replace(" ", "").lower()
    try:
        parsed = datetime.strptime(cleaned, "%I:%M%p")
    except ValueError:
        return None
    seen_local = seen_at.astimezone(tz)
    candidate = seen_local.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
    if candidate < seen_local:
        candidate += timedelta(days=1)
    return candidate.astimezone(seen_at.tzinfo or candidate.tzinfo)


def find_hard_stops(
    base: Path | None = None,
    now: datetime | None = None,
    lookback_days: float = DEFAULT_LOOKBACK_DAYS,
) -> list[HardStop]:
    """Every real rate-limit hit found in local transcripts (main + subagents).

    Bounded to files touched in the last ``lookback_days`` (see
    :data:`DEFAULT_LOOKBACK_DAYS`) using the same mtime-skip as
    :func:`rolling_window_usage` — measured live against 704 real local transcripts (~600MB
    incl. subagents): unbounded, this scan alone took over 40s, too slow to run before every
    subagent dispatch.
    """
    if now is None:
        now = datetime.now(UTC)
    cutoff = now - timedelta(days=lookback_days)
    paths = [p for p in all_local_transcripts(base=base) if _mtime_at_or_after(p, cutoff)]
    all_paths = list(paths)
    for p in paths:
        all_paths.extend(q for q in find_subagent_transcripts(p) if _mtime_at_or_after(q, cutoff))

    stops: list[HardStop] = []
    for path in all_paths:
        try:
            fh = open(path, encoding="utf-8")
        except OSError:
            continue
        with fh:
            for line in fh:
                if "session limit" not in line:
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                text = _message_text(record)
                match = _HARD_STOP_RE.search(text)
                if not match:
                    continue
                stamp = record.get("timestamp")
                if not isinstance(stamp, str):
                    continue
                try:
                    seen_at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                except ValueError:
                    continue
                reset_at = _resolve_reset_at(seen_at, match.group(1), match.group(2))
                if reset_at is None:
                    continue
                stops.append(HardStop(seen_at=seen_at, reset_at=reset_at, source=path))
    return stops


#: Calibration samples are capped to the most recent N stops. Each sample is itself a full
#: rolling_window_usage() call - measured live: with 11 real hard-stops on this machine and no
#: cap, that call alone ran 11 times, which was most of a ~52s pause_gate() call. Recent hits
#: are also more representative of current quota behavior than old ones.
MAX_CALIBRATION_SAMPLES = 5


def calibration_ceiling(
    now: datetime,
    window_hours: float = 5.0,
    base: Path | None = None,
    stops: list[HardStop] | None = None,
) -> float | None:
    """Average ``$``-equivalent spend observed immediately before each of the most recent
    :data:`MAX_CALIBRATION_SAMPLES` real hard-stops.

    Returns ``None`` with zero samples — callers must treat that as "no estimate
    available," never as 0% or 100% (fail open, see :func:`pause_gate`).

    ``stops``, when given, is used instead of calling :func:`find_hard_stops` again — that
    scan reads every local transcript, so a caller that already has the list (like
    :func:`pause_gate`, which needs it separately anyway) should pass it through rather than
    pay for a second full scan.
    """
    if stops is None:
        stops = find_hard_stops(base=base, now=now)
    if not stops:
        return None
    recent = sorted(stops, key=lambda s: s.seen_at, reverse=True)[:MAX_CALIBRATION_SAMPLES]
    samples = [rolling_window_usage(stop.seen_at, window_hours, base=base).cost_usd for stop in recent]
    return sum(samples) / len(samples)


def estimated_pct(
    now: datetime,
    window_hours: float = 5.0,
    base: Path | None = None,
    stops: list[HardStop] | None = None,
) -> float | None:
    """Estimated percent of the rolling window used, or ``None`` with no calibration data.

    ``stops`` is forwarded to :func:`calibration_ceiling` unchanged — see there.
    """
    ceiling = calibration_ceiling(now, window_hours, base=base, stops=stops)
    if ceiling is None or ceiling <= 0:
        return None
    spend = rolling_window_usage(now, window_hours, base=base).cost_usd
    return min(100.0, max(0.0, spend / ceiling * 100.0))


GATE_RUN, GATE_PAUSE = "RUN", "PAUSE"
PAUSE_THRESHOLD_PCT = 85.0


@dataclass(frozen=True)
class GateResult:
    """The pause/resume decision. ``reset_at``/``exact`` are only meaningful when PAUSE."""

    state: str
    reset_at: datetime | None
    reason: str
    exact: bool


def pause_gate(now: datetime, window_hours: float = 5.0, base: Path | None = None) -> GateResult:
    """RUN or PAUSE, preferring the exact hard-stop signal over the estimate.

    Fails OPEN (returns RUN) when there is no calibration data yet - a cold
    start must not block all work indefinitely on day one.
    """
    window_start = now - timedelta(hours=window_hours)
    all_stops = find_hard_stops(base=base, now=now)  # one full scan, reused - see calibration_ceiling
    active_stops = [s for s in all_stops if window_start <= s.seen_at <= now and s.reset_at > now]
    if active_stops:
        latest = max(active_stops, key=lambda s: s.seen_at)
        return GateResult(
            GATE_PAUSE,
            latest.reset_at,
            f"hit the account's session limit at {latest.seen_at.isoformat()}; resets {latest.reset_at.isoformat()}",
            True,
        )

    pct = estimated_pct(now, window_hours, base=base, stops=all_stops)
    if pct is None:
        return GateResult(GATE_RUN, None, "no calibration data yet - failing open", False)
    if pct >= PAUSE_THRESHOLD_PCT:
        return GateResult(
            GATE_PAUSE,
            now + timedelta(hours=window_hours),
            f"estimated {pct:.0f}% of the {window_hours:.0f}h window used "
            "(no exact reset time available - this is a best-effort estimate)",
            False,
        )
    return GateResult(GATE_RUN, None, f"estimated {pct:.0f}% of the {window_hours:.0f}h window used", False)


def main(argv: list[str] | None = None) -> int:
    import argparse

    force_utf8_output()

    ap = argparse.ArgumentParser(description="Rate-limit-window pause gate.")
    ap.add_argument("--window-hours", type=float, default=5.0)
    ap.add_argument("--gate", action="store_true", help="Print the state and exit 0=RUN, 1=PAUSE")
    args = ap.parse_args(argv)

    result = pause_gate(datetime.now(UTC), args.window_hours)
    print(f"{result.state}: {result.reason}")
    if result.reset_at is not None:
        print(f"  reset_at: {result.reset_at.isoformat()} (exact={result.exact})")
    return 0 if result.state == GATE_RUN else 1


if __name__ == "__main__":
    raise SystemExit(main())
