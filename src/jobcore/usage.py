"""Token/cost accounting for Claude Code sessions.

Reads a session transcript (the JSONL Claude Code writes under
``~/.claude/projects/<slug>/<session-id>.jsonl``), sums the token usage the API
reported on every assistant message, and prices it.

Why this exists: a job-seeking run (research + CV tailoring + gates for one
company) has a real dollar cost, and until now nothing in the repo could report
it. Point this at a transcript to get the spend for a whole session, or
``--by-turn`` to attribute it to individual requests.

Pricing is per million tokens. Cache reads bill at 0.1x the input rate; cache
writes bill at 1.25x for the 5-minute TTL and 2x for the 1-hour TTL, so the two
are counted separately using the ``cache_creation`` breakdown in the transcript.
Fast mode has its own rate (double standard), and promotional windows are
resolved per message against that message's own timestamp.

Spend from subagents is included by default: Claude Code writes each subagent to
a separate ``<session-id>/subagents/agent-*.jsonl`` file, so summing only the
parent transcript reported delegated work as costing exactly $0.

Also flags the **longest single continuous thread** (main session or one
subagent's own transcript) by API-message count. This is the evidence-gathering
trigger from claude-sdlc's ADR-0001 (schema-patched state vs. conversational
compaction): a thread this long is one continuous accumulating context with no
subagent boundary inside it, which is exactly the shape where the O(T^2) growth
that ADR describes would start to bite. Below the threshold, isolation/delegation
already keeps any one thread short enough not to matter.

CLI:
    uv run python -m src.jobcore.usage                      # current session
    uv run python -m src.jobcore.usage --by-turn
    uv run python -m src.jobcore.usage --no-subagents       # main context only
    uv run python -m src.jobcore.usage --transcript <path.jsonl> --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

from .console import force_utf8_output

# Input / output USD per million tokens. Cache multipliers are derived.
# Source: Anthropic published pricing (claude-api skill, cached 2026-06-24).
#
# Fast mode is priced as its OWN entry, not as a multiple of the base rate: it
# bills at $10/$50 on Opus, double standard Opus. The previous code stripped the
# `-fast` suffix and silently billed fast traffic at the standard rate — a 2x
# understatement, contradicting CLAUDE.md's own "fast mode is $10/$50" note.
# An unrecognised `-fast` model now falls through to `unpriced_messages`, which
# the report warns about loudly, rather than being quietly under-billed.
PRICING: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.00, 50.00),
    "claude-mythos-5": (10.00, 50.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-5-fast": (10.00, 50.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-8-fast": (10.00, 50.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

# Promotional rates that apply up to and including a cutoff date, after which
# the PRICING entry above takes over. Priced per MESSAGE against that message's
# own transcript timestamp, so a session spanning the cutoff bills correctly on
# both sides of it (and re-pricing an old transcript stays accurate).
INTRO_PRICING: dict[str, tuple[tuple[float, float], date]] = {
    "claude-sonnet-5": ((2.00, 10.00), date(2026, 8, 31)),
}

CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_5M_MULTIPLIER = 1.25
CACHE_WRITE_1H_MULTIPLIER = 2.0

_MILLION = 1_000_000


def normalize_model(model: str | None) -> str:
    """Strip the context-window suffix so ``claude-opus-5[1m]`` prices.

    Only ``[1m]``-style suffixes are dropped: the 1M context window carries no
    long-context premium on current models, so it bills at the standard rate.
    A ``-fast`` suffix is deliberately KEPT — fast mode costs double, and has
    its own PRICING entry.
    """
    if not model:
        return ""
    return re.sub(r"\[[^\]]*\]$", "", model.strip())


def rates_for(model: str | None, on: date | None = None) -> tuple[float, float] | None:
    """Return (input, output) USD per million tokens, or None if unknown.

    ``on`` is the date the tokens were billed, used to resolve promotional
    pricing windows; it defaults to today.
    """
    key = normalize_model(model)
    intro = INTRO_PRICING.get(key)
    if intro is not None:
        rates, until = intro
        if (on or date.today()) <= until:
            return rates
    return PRICING.get(key)


@dataclass
class Usage:
    """Token counts and cost for one or more assistant messages."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0
    messages: int = 0
    web_searches: int = 0
    web_fetches: int = 0
    cost_usd: float = 0.0
    unpriced_messages: int = 0
    models: dict[str, int] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_5m_tokens
            + self.cache_write_1h_tokens
        )

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_5m_tokens += other.cache_write_5m_tokens
        self.cache_write_1h_tokens += other.cache_write_1h_tokens
        self.messages += other.messages
        self.web_searches += other.web_searches
        self.web_fetches += other.web_fetches
        self.cost_usd += other.cost_usd
        self.unpriced_messages += other.unpriced_messages
        for k, v in other.models.items():
            self.models[k] = self.models.get(k, 0) + v


def usage_from_message(raw: dict, model: str | None, on: date | None = None) -> Usage:
    """Build a Usage (including cost) from one API ``usage`` object.

    ``on`` is the message's own billing date (from its transcript timestamp),
    used to resolve promotional pricing windows.
    """
    creation = raw.get("cache_creation") or {}
    write_1h = int(creation.get("ephemeral_1h_input_tokens") or 0)
    write_5m = int(creation.get("ephemeral_5m_input_tokens") or 0)
    total_write = int(raw.get("cache_creation_input_tokens") or 0)
    # Older transcripts omit the per-TTL split; attribute the remainder to 5m
    # (the default TTL) so nothing is silently dropped from the cost.
    if total_write > write_1h + write_5m:
        write_5m += total_write - write_1h - write_5m

    server = raw.get("server_tool_use") or {}
    u = Usage(
        input_tokens=int(raw.get("input_tokens") or 0),
        output_tokens=int(raw.get("output_tokens") or 0),
        cache_read_tokens=int(raw.get("cache_read_input_tokens") or 0),
        cache_write_5m_tokens=write_5m,
        cache_write_1h_tokens=write_1h,
        messages=1,
        web_searches=int(server.get("web_search_requests") or 0),
        web_fetches=int(server.get("web_fetch_requests") or 0),
    )
    key = normalize_model(model) or "unknown"
    u.models[key] = 1

    rates = rates_for(model, on)
    if rates is None:
        u.unpriced_messages = 1
        return u
    rate_in, rate_out = rates
    u.cost_usd = (
        u.input_tokens * rate_in
        + u.output_tokens * rate_out
        + u.cache_read_tokens * rate_in * CACHE_READ_MULTIPLIER
        + u.cache_write_5m_tokens * rate_in * CACHE_WRITE_5M_MULTIPLIER
        + u.cache_write_1h_tokens * rate_in * CACHE_WRITE_1H_MULTIPLIER
    ) / _MILLION
    return u


def _user_prompt_text(record: dict) -> str | None:
    """Return the visible text of a genuine user prompt, else None.

    Tool results and system reminders also arrive as ``type: "user"``; those are
    not turn boundaries, so they're filtered out.
    """
    if record.get("type") != "user":
        return None
    message = record.get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        if not parts:
            return None  # tool_result-only turn
        text = "\n".join(parts)
    else:
        return None
    text = re.sub(r"<system-reminder>.*?</system-reminder>", "", text, flags=re.S)
    text = text.strip()
    return text or None


def _label(text: str, width: int = 70) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= width else one_line[: width - 1] + "…"


def parse_transcript(path: str | Path) -> tuple[Usage, list[tuple[str, Usage]]]:
    """Sum usage across a transcript.

    Returns ``(total, turns)`` where ``turns`` pairs each user prompt's label
    with the usage of the assistant work that followed it.
    """
    total = Usage()
    turns: list[tuple[str, Usage]] = []
    current: Usage | None = None

    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # partially-flushed tail line

            prompt = _user_prompt_text(record)
            if prompt is not None:
                current = Usage()
                turns.append((_label(prompt), current))
                continue

            message = record.get("message") or {}
            raw = message.get("usage")
            if not isinstance(raw, dict):
                continue
            u = usage_from_message(raw, message.get("model"), _record_date(record))
            total.add(u)
            if current is not None:
                current.add(u)

    return total, turns


def _record_date(record: dict) -> date | None:
    """The billing date of one transcript record, or None if unparseable."""
    stamp = record.get("timestamp")
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def project_slug(cwd: str | Path) -> str:
    """Map a working directory to its Claude Code project-dir slug."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(cwd))


def find_transcript(cwd: str | Path | None = None) -> Path | None:
    """Locate the most recently modified transcript for ``cwd``."""
    cwd = Path(cwd or os.getcwd())
    base = Path.home() / ".claude" / "projects" / project_slug(cwd)
    if not base.is_dir():
        return None
    candidates = [p for p in base.glob("*.jsonl") if p.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def find_subagent_transcripts(transcript: str | Path) -> list[Path]:
    """Locate the transcripts of subagents spawned from ``transcript``.

    Claude Code writes each subagent to its own file under
    ``<project-dir>/<session-id>/subagents/agent-*.jsonl`` — NOT into the parent
    transcript. Sidechain records carry no ``usage``, so summing only the parent
    reported subagent spend as exactly $0.00 and made delegation look free. Real
    delegated dollars live in these files.
    """
    d = Path(transcript).with_suffix("") / "subagents"
    return sorted(d.glob("*.jsonl")) if d.is_dir() else []


def parse_subagents(transcript: str | Path) -> tuple[Usage, int]:
    """Sum usage across every subagent spawned from ``transcript``.

    Returns ``(usage, transcript_count)``. Subagent tokens are real spend but do
    NOT sit in the main context, so callers report them separately rather than
    letting them distort the per-round-trip context metric.
    """
    total = Usage()
    paths = find_subagent_transcripts(transcript)
    for p in paths:
        try:
            sub, _ = parse_transcript(p)
        except OSError:  # pragma: no cover - unreadable sidecar
            continue
        total.add(sub)
    return total, len(paths)


#: Sequential API messages in one continuous thread (no subagent boundary inside
#: it) above which schema-patched state would start to outperform conversational
#: accumulation, per claude-sdlc's ADR-0001. Below this, isolation/delegation
#: already keeps every thread here short enough that it doesn't matter.
LONG_THREAD_THRESHOLD = 20


def longest_single_thread(transcript: str | Path) -> tuple[str, int]:
    """The longest unbroken autonomous run: API round-trips with no human input
    and no subagent boundary in between.

    For the main transcript this is the largest single entry in ``turns`` — the
    stretch between two human prompts — NOT the whole session's message total.
    A long interactive session naturally racks up many turns over an hour of
    back-and-forth (already mitigated by /compact and /checkpoint); what this
    is actually hunting for is one turn where the model kept working
    autonomously for many steps before handing back, which is the shape the
    O(T^2) growth in claude-sdlc's ADR-0001 describes. A subagent's own
    transcript has no interleaved human prompts at all — it IS one continuous
    autonomous run end to end — so its whole-file total is used directly.
    """
    _, main_turns = parse_transcript(transcript)
    longest_label = "main session (one turn)"
    longest_count = max((u.messages for _, u in main_turns), default=0)
    for p in find_subagent_transcripts(transcript):
        try:
            sub_total, _ = parse_transcript(p)
        except OSError:  # pragma: no cover - unreadable sidecar
            continue
        if sub_total.messages > longest_count:
            longest_label, longest_count = p.stem, sub_total.messages
    return longest_label, longest_count


def marginal_cost_per_request(total: Usage) -> float | None:
    """USD of cache-read incurred by one additional request at this context size.

    Every request re-sends the whole prefix, billed as cache-read, so the cost of
    an extra tool call is (average context) x (cache-read rate) — independent of
    how much it outputs. Returns None when there is nothing to average over, or
    when the model mix has no pricing entry.
    """
    if total.messages == 0 or total.cache_read_tokens == 0:
        return None
    # Weight the input rate by how many messages ran on each priced model.
    weighted = 0.0
    count = 0
    for model, messages in total.models.items():
        rates = rates_for(model)
        if rates is None:
            continue
        weighted += rates[0] * messages
        count += messages
    if count == 0:
        return None
    avg_rate_in = weighted / count
    avg_context = total.cache_read_tokens / total.messages
    return avg_context * avg_rate_in * CACHE_READ_MULTIPLIER / _MILLION


# Context-size gate thresholds, expressed in MARGINAL USD PER ROUND TRIP rather
# than raw tokens. Adapted from claude-sdlc's Context Size Gate (which uses
# 100K/150K token thresholds and a chars/4 estimate); we key on measured cost
# instead, because the thing worth bounding is spend, and a 1M-context model
# makes a raw token ceiling the wrong signal.
GATE_ADVISE_USD = 0.10  # ~200K context on Opus — checkpoint soon
GATE_STOP_USD = 0.20  # ~400K context on Opus — checkpoint and /clear now

# Absolute-spend arm. The marginal-cost arm above only sees cache READS
# (context re-sent per round trip), so it is structurally blind to one-shot
# INGESTION: a single large skill/doc load bills as cache WRITES at 1.25-2x and
# never raises the per-round-trip figure. Measured 2026-07-28: a session sat at
# a healthy $0.030/round trip while one `claude-api` skill load pushed it to
# $15.85 total, 73% of which was cache-writes. A gate that only watches the
# marginal rate says OK the whole way down. This arm watches the bill itself.
GATE_ADVISE_SPEND_USD = 10.0
GATE_STOP_SPEND_USD = 20.0

GATE_OK, GATE_ADVISE, GATE_STOP = "OK", "ADVISE", "STOP"

_GATE_RANK = {GATE_OK: 0, GATE_ADVISE: 1, GATE_STOP: 2}


def _blended_rates(total: Usage) -> tuple[float, float] | None:
    """(input, output) USD/M averaged over how many messages ran on each model."""
    weighted_in = weighted_out = 0.0
    priced = 0
    for model, messages in total.models.items():
        rates = rates_for(model)
        if rates is None:
            continue
        weighted_in += rates[0] * messages
        weighted_out += rates[1] * messages
        priced += messages
    if priced == 0:
        return None
    return weighted_in / priced, weighted_out / priced


def cost_shares(total: Usage) -> tuple[float, float, float] | None:
    """Split spend into (cache-write, cache-read, output) fractions.

    The report previously showed token COUNTS but never cost shares, so a session
    that was 73% cache-writes looked unremarkable. These three are what actually
    tell you which lever to pull.
    """
    if total.cost_usd <= 0:
        return None
    rates = _blended_rates(total)
    if rates is None:
        return None
    rate_in, rate_out = rates
    write = (
        (
            total.cache_write_5m_tokens * CACHE_WRITE_5M_MULTIPLIER
            + total.cache_write_1h_tokens * CACHE_WRITE_1H_MULTIPLIER
        )
        * rate_in
        / _MILLION
    )
    read = total.cache_read_tokens * rate_in * CACHE_READ_MULTIPLIER / _MILLION
    out = total.output_tokens * rate_out / _MILLION
    c = total.cost_usd
    return write / c, read / c, out / c


def ingestion_share(total: Usage) -> float | None:
    """Fraction of spend that went on cache WRITES (pulling content in).

    High share means the session is dominated by ingesting material, not by
    re-sending context — a different problem with a different fix (load less,
    versus /compact or delegate).
    """
    shares = cost_shares(total)
    return None if shares is None else shares[0]


def context_gate(total: Usage, subagents: Usage | None = None) -> tuple[str, str]:
    """Classify how expensive continuing this session has become.

    Returns (state, human message). States: OK / ADVISE / STOP — the worst of
    two arms: marginal cost per round trip, and total spend (which catches the
    ingestion-heavy sessions the marginal arm cannot see). ``subagents`` folds
    delegated spend into the total, since those are real dollars too.

    Mirrors claude-sdlc's advisory-then-HITL structure: never auto-abandons work,
    just tells the caller when ending the session is cheaper than continuing.
    """
    spent = total.cost_usd + (subagents.cost_usd if subagents else 0.0)
    marginal = marginal_cost_per_request(total)

    if marginal is None and spent <= 0:
        return GATE_OK, "no priced traffic yet — nothing to gate on"

    arms: list[tuple[str, str]] = []

    if marginal is not None:
        if marginal >= GATE_STOP_USD:
            arms.append(
                (
                    GATE_STOP,
                    f"each round trip now costs ~${marginal:.3f}. Checkpoint and /clear: "
                    "run /checkpoint, then start a fresh session from the resume prompt. "
                    "Continuing here is the expensive option.",
                )
            )
        elif marginal >= GATE_ADVISE_USD:
            arms.append(
                (
                    GATE_ADVISE,
                    f"each round trip now costs ~${marginal:.3f}. Consider /compact at the "
                    "next phase boundary, or delegate the next mechanical batch to a subagent.",
                )
            )
        else:
            arms.append((GATE_OK, f"each round trip costs ~${marginal:.3f} — healthy"))

    if spent >= GATE_STOP_SPEND_USD:
        arms.append(
            (
                GATE_STOP,
                f"this session has already spent ${spent:.2f}. Run /checkpoint and /clear — "
                "a fresh session re-reads only what it needs.",
            )
        )
    elif spent >= GATE_ADVISE_SPEND_USD:
        arms.append(
            (
                GATE_ADVISE,
                f"this session has already spent ${spent:.2f}. Wrap up the current phase "
                "rather than opening a new one, and avoid loading further large skills or docs.",
            )
        )

    state, msg = max(arms, key=lambda a: _GATE_RANK[a[0]])
    share = ingestion_share(total)
    if share is not None and share >= 0.5:
        msg += f" ({share:.0%} of spend is cache-writes — ingestion, not round trips)"
    return state, msg


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def format_report(
    total: Usage,
    turns: list[tuple[str, Usage]],
    by_turn: bool,
    subagents: Usage | None = None,
    subagent_count: int = 0,
    long_thread: tuple[str, int] | None = None,
) -> str:
    lines = ["## Session cost", ""]
    combined = total.cost_usd + (subagents.cost_usd if subagents else 0.0)
    if subagents and subagents.messages:
        lines.append(f"- **Cost: ${combined:.2f}** (main ${total.cost_usd:.2f} + subagents ${subagents.cost_usd:.2f})")
    else:
        lines.append(f"- **Cost: ${total.cost_usd:.2f}**")
    lines.append(f"- Tokens: {_fmt_tokens(total.total_tokens)} total ({total.messages} API messages)")
    lines.append(
        f"  - fresh input {_fmt_tokens(total.input_tokens)} · "
        f"output {_fmt_tokens(total.output_tokens)} · "
        f"cache read {_fmt_tokens(total.cache_read_tokens)} · "
        f"cache write {_fmt_tokens(total.cache_write_5m_tokens + total.cache_write_1h_tokens)}"
    )
    if total.web_searches or total.web_fetches:
        lines.append(
            f"  - server tools: {total.web_searches} web searches, {total.web_fetches} web fetches (billed separately)"
        )
    if total.models:
        lines.append("  - models: " + ", ".join(f"{m} ×{c}" for m, c in sorted(total.models.items())))
    if total.unpriced_messages:
        lines.append(
            f"  - ⚠️  {total.unpriced_messages} messages had no pricing entry and are EXCLUDED from the cost above"
        )

    shares = cost_shares(total)
    if shares is not None:
        write_s, read_s, out_s = shares
        lines += [
            "",
            "### Where the money went",
            "",
            f"- Cache writes (ingesting content): **{write_s:.0%}**",
            f"- Cache reads (re-sending context): **{read_s:.0%}**",
            f"- Output generation: **{out_s:.0%}**",
        ]
        if write_s >= 0.5:
            lines.append(
                "- This session was dominated by **ingestion**, not round trips — the lever is "
                "loading less (skills, docs, large files), not /compact or shorter replies."
            )

    if subagents and subagents.messages:
        lines += [
            "",
            "### Delegated to subagents",
            "",
            f"- **${subagents.cost_usd:.2f}** across {subagent_count} subagent(s), "
            f"{subagents.messages} API messages, {_fmt_tokens(subagents.total_tokens)} tokens",
            "- These tokens never entered this session's context — but they are real spend.",
            "  Delegation only pays when the output it avoids is much larger than the briefing",
            "  you write plus the report you read back.",
        ]
        if subagents.models:
            lines.append("  - models: " + ", ".join(f"{m} ×{c}" for m, c in sorted(subagents.models.items())))

    marg = marginal_cost_per_request(total)
    if marg is not None:
        avg_ctx = total.cache_read_tokens / total.messages
        lines += [
            "",
            "### Marginal cost of one more round trip",
            "",
            f"- Average context re-read per request: **{_fmt_tokens(int(avg_ctx))}**",
            f"- So each additional tool call costs about **${marg:.3f}** in cache reads alone,",
            "  before it generates a single output token. Fewer round trips is the lever;",
            "  shorter answers are not.",
        ]
        state, msg = context_gate(total, subagents)
        icon = {GATE_OK: "🟢", GATE_ADVISE: "🟡", GATE_STOP: "🔴"}[state]
        lines.append(f"- {icon} **{state}** — {msg}")

    if long_thread is not None and long_thread[1] >= LONG_THREAD_THRESHOLD:
        label, count = long_thread
        lines += [
            "",
            f"- 🟠 **LONG SINGLE THREAD**: {label} ran {count} sequential API turns with no "
            f"subagent boundary (threshold {LONG_THREAD_THRESHOLD}) — the build trigger for "
            "schema-patched state over conversational accumulation. See claude-sdlc ADR-0001.",
        ]

    if by_turn and turns:
        lines += ["", "### By turn", ""]
        lines.append("| # | Cost | Tokens | Prompt |")
        lines.append("|---|------|--------|--------|")
        for i, (label, u) in enumerate(turns, 1):
            lines.append(f"| {i} | ${u.cost_usd:.2f} | {_fmt_tokens(u.total_tokens)} | {label} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    # force_utf8_output() supersedes the old local `_make_stdout_lossy()`: it sets
    # errors='replace' AND encoding='utf-8', so the gate icons encode rather than
    # degrade to '?'. Keeping both left two competing fixes for one bug.
    force_utf8_output()
    ap = argparse.ArgumentParser(description="Report token usage and cost for a session.")
    ap.add_argument(
        "--transcript",
        help="Path to a session .jsonl (default: newest for the current directory)",
    )
    ap.add_argument("--by-turn", action="store_true", help="Break the cost down per prompt")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of markdown")
    ap.add_argument(
        "--gate",
        action="store_true",
        help="Context-size gate: print the state and exit 0=OK, 1=ADVISE, 2=STOP",
    )
    ap.add_argument(
        "--no-subagents",
        action="store_true",
        help="Exclude spend from subagent transcripts (main context only)",
    )
    args = ap.parse_args(argv)

    path = Path(args.transcript) if args.transcript else find_transcript()
    if path is None or not path.is_file():
        print(
            "No transcript found. Pass --transcript <path.jsonl>.",
        )
        return 1

    total, turns = parse_transcript(path)
    subagents, subagent_count = (Usage(), 0) if args.no_subagents else parse_subagents(path)
    combined = total.cost_usd + subagents.cost_usd
    long_thread = longest_single_thread(path)

    if args.gate:
        state, msg = context_gate(total, subagents)
        marg = marginal_cost_per_request(total)
        avg = total.cache_read_tokens / total.messages if total.messages else 0
        spend = f"${combined:.2f}"
        if subagents.messages:
            spend += f" (incl. ${subagents.cost_usd:.2f} subagents)"
        print(
            f"{state}: {msg}\n"
            f"  context/request ~{_fmt_tokens(int(avg))} | spent {spend} | "
            f"marginal ${marg:.3f}/round trip"
            if marg is not None
            else f"{state}: {msg}\n  spent {spend}"
        )
        return {GATE_OK: 0, GATE_ADVISE: 1, GATE_STOP: 2}[state]

    if args.json:
        payload = {
            "transcript": str(path),
            "total": asdict(total),
            "total_tokens": total.total_tokens,
            "subagents": asdict(subagents),
            "subagent_transcripts": subagent_count,
            "combined_cost_usd": combined,
            "turns": [{"label": label, "cost_usd": u.cost_usd, "tokens": u.total_tokens} for label, u in turns],
            "longest_single_thread": {"label": long_thread[0], "messages": long_thread[1]},
        }
        print(json.dumps(payload, indent=2))
    else:
        print(format_report(total, turns, args.by_turn, subagents, subagent_count, long_thread))
        print(f"\n_Source: {path.name}_")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
