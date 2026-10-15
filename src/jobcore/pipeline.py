"""Pipeline ledger — the durable, decision-aware worklist.

Answers the only question the old shortlist could not: *which company should I spend my next
hour on?* The shortlist was stateless, so it re-presented roles that had already rejected the
candidate, roles he had declined, and roles a rung above his band — every run, forever.

Two ideas carry the whole design:

**1. State truth lives here; content truth lives in ``positions/``.** The ledger is standalone
because opportunities worth tracking often have no folder yet (a link pasted this morning). A
row may point at a folder via ``position_folder``, but never depends on one.

**2. Pursuit state and posting liveness are different facts.** ``stage``/``outcome`` are the
candidate's side ("I applied", "they rejected me"). ``posting_live`` is the market's side ("the
URL still resolves"). Conflating them is why the old sweep reported closed pursuits as OPEN
while alarming on the two most active ones. A row can legitimately be ``IN_PROCESS`` with a
``CLOSED`` posting — that is a normal late-stage pursuit, not an emergency.

**3. A req can reach you by more than one route, and that matters.** ``channels`` is an
append-only provenance log (scan, head-hunter *by name*, friend referral, inbound recruiter,
interview lead…), and ``submissions`` records where the CV actually went. Grip Security arrived
via a scan on 07-25 and was then pitched by a head-hunter on 07-30; without this, the same CV
could go out twice through competing routes, which invites an agency fee dispute or a
duplicate-application flag. :func:`record_submission` refuses a second route by default.

Note ``posted_date`` (when the JD went live on the board) is deliberately separate from
``first_seen`` (when it reached you) — a three-week-old req you just discovered is a different
situation from one posted this morning.

Field ownership is the operating discipline:

===================  ==========  =====================================================
Field                Owner       Rule
===================  ==========  =====================================================
company/role/url     tool        set on insert, refreshed on upsert
job_id/source        tool        refreshed on upsert
location             tool        refreshed on upsert
posted_date          tool        when the JD went live on the board
score/level_band     tool        recomputed every run (scoring may improve)
score_verified       **human**   when true, freezes ``score`` against future upserts — the
                                 auto-scorer only sees a thin ATS-list snippet/title, and a
                                 human who read the full JD can be right where it is wrong.
                                 Confirmed 2026-08-09: Aidoc's Backend Tech Lead/Staff SWE/
                                 Principal reqs auto-scored 5 (no leadership+AI title floor
                                 applies) despite JDs dense with matching keywords the snippet
                                 never carried. Never auto-set; a human sets it after reading
                                 the actual posting.
score_note           **human**   freeform rationale for a verified score; paired with
                                 score_verified so the "why" survives next to the number.
posting_live         tool        a market fact; written by track_sweep OR by a poll that
                                 carried a conclusive liveness reading. Never blanked by a
                                 poll that lacks one.
first_seen           tool        set once on insert, never rewritten
last_touched         tool        bumped on any change
stage/outcome        **human**   upsert must NEVER overwrite
history              **human**   append-only, via set_stage. OUTCOMES only — how a pursuit
                                 ENDED. Read across siblings by prior_outcomes/bucket.
events               **human**   append-only, via add_event (and set_stage for APPLIED).
                                 Dated rounds along the way. Row-local: deliberately NOT
                                 history, or one HR screen would send every sibling req at
                                 that company to WATCH. See the EVENTS docstring.
risks                **human**   append-only, via add_risk. The "watch for" list; row-local
channels             mixed       append-only; scans add their own, humans add people
submissions          **human**   append-only, via record_submission; guarded
position_folder      **human**   upsert must NEVER overwrite
company_policy       **human**   only a human sets do_not_pursue
===================  ==========  =====================================================

Deterministic: zero model calls, stdlib only.

CLI::

    uv run python -m src.jobcore.pipeline upsert --from positions/_ats_shortlist.json
    uv run python -m src.jobcore.pipeline add --url URL --company X --role Y [--score N]
    uv run python -m src.jobcore.pipeline set <id> --stage APPLIED
    uv run python -m src.jobcore.pipeline set <id> --stage CLOSED --outcome THEY_REJECTED --note "..."
    uv run python -m src.jobcore.pipeline channel <id> --channel head_hunter --name "Noa"
    uv run python -m src.jobcore.pipeline submit <id> --channel head_hunter --name "Noa"
    uv run python -m src.jobcore.pipeline policy <company-slug> --do-not-pursue --reason "..."
    uv run python -m src.jobcore.pipeline render [--out job-tracking/PIPELINE_BOARD.md]
    uv run python -m src.jobcore.pipeline validate

Exit codes: ``0`` OK · ``1`` invalid ledger / rejected mutation (including a blocked
second-route submission).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .console import force_utf8_output
from .csv_ingest import REPO_ROOT, slugify
from .scoring import depth_risk_flag, is_relevant, level_band

SCHEMA_VERSION = "1.0"

#: Pursuit progress. Deliberately excludes the word "PASS" — existing docs used it for BOTH
#: "I declined" (`PASS / DECLINE`) and "they advanced me" (`PASSED, advanced`).
STAGES = ("NEW", "RESEARCHED", "APPLIED", "IN_PROCESS", "OFFER", "CLOSED")

#: Why a pursuit ended. Only meaningful when ``stage == "CLOSED"``.
OUTCOMES = ("THEY_REJECTED", "I_DECLINED", "POSTING_CLOSED", "NO_RESPONSE")

#: How thoroughly this position has been researched. Optional — absence reads as
#: "quick" everywhere it's consumed. "deep" is set by company-researcher/interview-prep
#: themselves once a deep-mode run completes; nothing sets it automatically.
RESEARCH_DEPTHS = ("quick", "deep")

#: Dated rounds along the way, recorded in ``events`` — a **separate list from ``history``**.
#:
#: ``history`` is not a general timeline: :func:`bucket` sends a row to WATCH whenever
#: :func:`prior_outcomes` finds history on any *sibling* row at the same company, and
#: :func:`_is_untouched` reads it as "a human decided something". Logging "HR screen on the 13th"
#: there would therefore drag every other req at that company into WATCH — the same trap
#: :func:`set_liveness` refuses for link checks. An interview round is something that *happened*;
#: only an outcome says how a pursuit *ended*, and only outcomes carry across siblings.
EVENTS = (
    "APPLIED",
    "HR_SCREEN",
    "TECH_SCREEN",
    "HOME_ASSIGNMENT",
    "SYSTEM_DESIGN",
    "MANAGER_ROUND",
    "TEAM_ROUND",
    "FINAL_ROUND",
    "OFFER_RECEIVED",
    "FOLLOW_UP_SENT",
)

#: EVENTS values that represent a round actually happening — drives the depth-escalation
#: suggestion in the `event` CLI command. Excludes APPLIED (not a round) and
#: OFFER_RECEIVED/FOLLOW_UP_SENT (too late for prep to matter).
INTERVIEW_SIGNAL_EVENTS = set(EVENTS) - {"APPLIED", "OFFER_RECEIVED", "FOLLOW_UP_SENT"}

#: Market fact about the posting URL — never implies pursuit state.
#:
#: ``LISTED`` is a deliberate FOURTH state, not a relabelled ``UNVERIFIED``. A Comeet or
#: Greenhouse row comes back from the board's own JSON API, so the req is in that board's
#: current listing by construction — but the public apply page is client-rendered and no
#: HTTP-only checker can read it, which is why all 24 Comeet rows sat at ``UNVERIFIED`` on
#: 2026-08-05 while being demonstrably live. Collapsing that into ``OPEN`` would flip a
#: binary rule on softer evidence, which is the mistake feedback_unverified_is_not_open
#: exists to prevent; a distinct state keeps *how we know* on the record. Both ``OPEN`` and
#: ``LISTED`` mean "you may apply"; only ``OPEN`` means a fetch confirmed the URL itself.
LIVENESS = ("OPEN", "LISTED", "CLOSED", "UNVERIFIED")

#: Board providers whose listing is an authoritative JSON API, so presence in a poll's
#: results justifies ``LISTED``. LinkedIn is absent on purpose: the guest scrape reads
#: rendered search cards, which go stale and get rate-limited, so it earns no upgrade.
API_LISTED_SOURCES = ("greenhouse", "comeet")

#: Careers hosts that render the apply-status banner CLIENT-SIDE, so no HTTP-only checker can
#: read it — and whose raw HTML additionally embeds ATS disposition labels ("requisition closed",
#: "position no longer available") that a substring match would misread as CLOSED for *every*
#: job on the site. Both a direct fetch and an Exa mirror were tried on Amdocs and neither could
#: tell open from closed. These need a human eyeball; the board must say so instead of implying
#: the role is actionable. (Amdocs runs Phenom/vscdn.)
JS_RENDERED_HOSTS = ("jobs.amdocs.com", "phenompeople.com", "myworkdayjobs.com")

#: Outcomes that keep a role out of the ACT NOW queue. ``POSTING_CLOSED``/``NO_RESPONSE`` are
#: absent on purpose: a reposted req or a revived thread is worth acting on again.
BLOCKING_OUTCOMES = ("THEY_REJECTED", "I_DECLINED")

#: How an opportunity reached the pipeline. Recorded append-only in ``channels`` — a single req
#: routinely arrives more than once by different routes, and which route you *use* has real
#: consequences (see :func:`route_conflict`).
CHANNELS = (
    "ats_scan",  # targeted board poll (ats_targets.json)
    "job_radar_delta",  # routine delta scan
    "job_radar_fresh",  # --from-scratch rescan
    "market_scan",  # company-agnostic keyword scan
    "head_hunter",  # external agency recruiter — `name` REQUIRED
    "recruiter_inbound",  # the company's own recruiter reached out — `name` expected
    "friend_referral",  # a friend shared it / offered to refer — `name` expected
    "direct_outreach",  # you approached them cold
    "interview_lead",  # you heard about it during another interview
    "manual",  # pasted link, no richer provenance
)

#: Routes where a *person* effectively owns the submission. Two of these on one req — or one of
#: these plus your own direct route — means submitting twice can trigger an agency fee dispute or
#: a duplicate-application flag at the employer. This is the Grip case: already in the pipeline
#: from a scan, then pitched by a head-hunter days later.
ATTRIBUTED_CHANNELS = ("head_hunter", "recruiter_inbound", "friend_referral")

#: Stages that mean "already in motion" rather than "decide whether to start".
IN_FLIGHT_STAGES = ("APPLIED", "IN_PROCESS", "OFFER")

#: How many ACT NOW rows to print before collapsing the tail. The board is a decision queue for
#: the next hour, not an inventory — 39 un-started rows listed in full is a dump nobody reads.
ACT_NOW_LIMIT = 12

LEDGER_DEFAULT = REPO_ROOT / "job-tracking" / "pipeline.json"
BOARD_DEFAULT = REPO_ROOT / "job-tracking" / "PIPELINE_BOARD.md"

#: Refreshed on every upsert. Everything else on a row is human-owned — see the module docstring.
_TOOL_FIELDS = (
    "company",
    "role",
    "url",
    "job_id",
    "source",
    "location",
    "score",
    "level_band",
    "depth_risk",
    "posted_date",
    # The board's department label. Stored so the relevance gate can be RE-APPLIED later
    # (see :func:`prune`) — without it, a gate fix could only ever affect future intake,
    # and the 34 noise rows the 2026-08-05 poll inserted would have been unreachable.
    "domain",
)

_REQUIRED_KEYS = (
    "id",
    "company",
    "company_slug",
    "role",
    "stage",
    "outcome",
    "posting_live",
    "history",
    "channels",
    "submissions",
)


def today_str() -> str:
    """Today in the profile's timezone. Never bare UTC — a human reads these dates."""
    from .scoring import default_profile

    return datetime.now(ZoneInfo(default_profile().timezone)).date().isoformat()


# --- load / save -------------------------------------------------------------


def _skeleton() -> dict:
    return {"schema_version": SCHEMA_VERSION, "company_policy": {}, "opportunities": []}


def load(path: Path | str = LEDGER_DEFAULT) -> dict:
    """Read the ledger. A missing file is normal (first run) → empty skeleton."""
    p = Path(path)
    if not p.exists():
        return _skeleton()
    data = json.loads(p.read_text(encoding="utf-8"))
    data.setdefault("schema_version", SCHEMA_VERSION)
    data.setdefault("company_policy", {})
    data.setdefault("opportunities", [])
    return data


def save(ledger: dict, path: Path | str = LEDGER_DEFAULT) -> None:
    """Write the ledger. Committed to git, so keep it diff-friendly and UTF-8 readable."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# --- identity / upsert -------------------------------------------------------


def make_id(company: str, role: str) -> str:
    """Stable human-readable row id, e.g. ``blinkops:backend-team-lead``."""
    return f"{slugify(company)}:{slugify(role)}"


def canon_key(company: str, role: str) -> tuple[str, str]:
    """Punctuation-blind identity for a req: ``(company_slug, normalized role)``.

    The last-resort match key, weaker than ``job_id`` and ``id`` but immune to the
    punctuation variants that forked the Cyera row on 2026-08-05 ("Team Lead, AI Team"
    vs "Team Lead - AI team"). Uses the same normalization the suppression export
    already relies on, so the two agree by construction.
    """
    return (slugify(company or ""), _norm_role(role or ""))


def _row_canon_key(opp: dict) -> tuple[str, str]:
    """``canon_key`` for a ledger row, tolerating rows whose company_slug predates a rename."""
    return canon_key(opp.get("company") or opp.get("company_slug") or "", opp.get("role") or "")


def _find(ledger: dict, *, job_id: str = "", opp_id: str = "", company: str = "", role: str = "") -> dict | None:
    """Match an existing row: by provider job_id, else by id, else by canonical key.

    job_id wins so a re-titled req updates in place instead of forking a duplicate row.
    The canonical-key fallback catches the opposite failure — the *same* title spelled
    with different punctuation, and rows whose ids were written before the slugify fix
    (``...lead---ai-team``), which would otherwise fork exactly once more.
    """
    jid = (job_id or "").strip().lower()
    if jid:
        for o in ledger["opportunities"]:
            if (o.get("job_id") or "").strip().lower() == jid:
                return o
    if opp_id:
        for o in ledger["opportunities"]:
            if o.get("id") == opp_id:
                return o
    if company and role:
        want = canon_key(company, role)
        for o in ledger["opportunities"]:
            if _row_canon_key(o) == want:
                return o
    return None


def _normalize(row: dict) -> dict:
    """Map a shortlist-shaped row (title/match_score) onto ledger field names."""
    company = row.get("company") or ""
    role = row.get("role") or row.get("title") or ""
    return {
        "company": company,
        "role": role,
        "url": row.get("url", ""),
        "job_id": (row.get("job_id") or "").strip(),
        "source": row.get("source", ""),
        "location": row.get("location", ""),
        "domain": row.get("domain", ""),
        "score": row.get("score", row.get("match_score", 5)),
        "level_band": row.get("level_band") or level_band({"title": role}),
        # Advisory only, same contract as level_band — recomputed from the title every run, never
        # gates or reorders anything. Driven by ``roles.depth_risk_titles`` in the profile; empty
        # means no opinion. See scoring.depth_risk_flag.
        "depth_risk": depth_risk_flag({"title": role}),
        # When the JD went live on the board — distinct from first_seen (when YOU found it).
        "posted_date": row.get("posted_date") or row.get("posted_str", ""),
        # The ATS poll already liveness-checks every row (Pass A, same check_url as the sweep).
        # Discarding it meant even freshly-verified Greenhouse rows rendered as "unconfirmed",
        # which trains you to ignore the marker — so carry it through when it is conclusive.
        "posting_live": row.get("liveness") if row.get("liveness") in LIVENESS else None,
    }


def upsert(ledger: dict, rows: list[dict], today: str | None = None, channel: str = "ats_scan") -> tuple[int, int]:
    """Merge scanner rows into the ledger. Returns ``(inserted, updated)``.

    Refreshes only :data:`_TOOL_FIELDS` on an existing row — ``stage``, ``outcome``,
    ``history``, ``submissions``, ``position_folder`` and ``posting_live`` are never touched here,
    so a nightly poll can never undo a human's decision or a sweep's liveness reading.

    ``channel`` records HOW these rows arrived; it is appended (deduped) to each row's
    ``channels`` list so a req that a scan already knew about, and that a head-hunter later
    pitches, ends up carrying both routes.
    """
    today = today or today_str()
    inserted = updated = 0
    for raw in rows:
        norm = _normalize(raw)
        if not norm["company"] or not norm["role"]:
            continue
        opp_id = make_id(norm["company"], norm["role"])
        # A conclusive liveness reading from the poll; None when the row carried none.
        live = norm.pop("posting_live", None)
        existing = _find(ledger, job_id=norm["job_id"], opp_id=opp_id, company=norm["company"], role=norm["role"])
        if existing is None:
            ledger["opportunities"].append(
                {
                    "id": opp_id,
                    "company_slug": slugify(norm["company"]),
                    **norm,
                    "stage": "NEW",
                    "outcome": None,
                    "posting_live": live or "UNVERIFIED",
                    "first_seen": today,
                    "last_touched": today,
                    "position_folder": None,
                    "history": [],
                    "events": [],
                    "risks": [],
                    "channels": [],
                    "submissions": [],
                }
            )
            existing = ledger["opportunities"][-1]
            inserted += 1
        else:
            for f in _TOOL_FIELDS:
                if f == "score" and existing.get("score_verified"):
                    continue  # a human read the full JD; don't let a thin snippet re-score it
                existing[f] = norm[f]
            existing["company_slug"] = slugify(norm["company"])
            if live:
                existing["posting_live"] = live
            existing["last_touched"] = today
            updated += 1
        existing.setdefault("channels", [])
        existing.setdefault("submissions", [])
        existing.setdefault("events", [])
        existing.setdefault("risks", [])
        if channel:
            add_channel(ledger, existing["id"], channel, today=today)
    return inserted, updated


# --- provenance: how it reached you, and where the CV actually went ------------


def add_channel(
    ledger: dict,
    opp_id: str,
    channel: str,
    name: str = "",
    today: str | None = None,
    note: str = "",
    chosen: bool = False,
) -> dict:
    """Append a provenance entry (append-only, deduped on ``(channel, name)``).

    ``chosen=True`` marks this as *the* route to use, resolving a multi-route ambiguity and
    clearing the board warning. Only one channel can be chosen — setting it moves the mark.

    Raises ``ValueError`` for an unknown channel, or for an :data:`ATTRIBUTED_CHANNELS` route with
    no ``name`` — an unnamed head-hunter is exactly the ambiguity that causes a double submission.
    """
    today = today or today_str()
    if channel not in CHANNELS:
        raise ValueError(f"unknown channel {channel!r} — must be one of {', '.join(CHANNELS)}")
    if channel in ATTRIBUTED_CHANNELS and not name.strip():
        raise ValueError(f"channel {channel!r} requires --name (who); an unnamed route cannot be de-duplicated")
    opp = _find(ledger, opp_id=opp_id)
    if opp is None:
        raise KeyError(f"no opportunity with id {opp_id!r}")
    opp.setdefault("channels", [])
    key = (channel, name.strip().lower())
    entry = next((c for c in opp["channels"] if (c.get("channel"), (c.get("name") or "").strip().lower()) == key), None)
    if entry is None:
        entry = {"date": today, "channel": channel, "name": name.strip(), "note": note}
        opp["channels"].append(entry)
    elif note:
        entry["note"] = note
    if chosen:
        for c in opp["channels"]:
            c.pop("chosen", None)
        entry["chosen"] = True
    opp["last_touched"] = today
    return opp


def routes(opp: dict) -> list[dict]:
    """The distinct person-owned routes into this opportunity."""
    return [c for c in (opp.get("channels") or []) if c.get("channel") in ATTRIBUTED_CHANNELS]


def chosen_route(opp: dict) -> dict | None:
    """The route explicitly picked to go through, if the ambiguity has been resolved."""
    return next((c for c in (opp.get("channels") or []) if c.get("chosen")), None)


def route_conflict(opp: dict) -> bool:
    """True when this req is reachable by more than one route and none has been chosen yet.

    That is the state in which sending a CV twice does real damage: two agencies can both claim
    the introduction, or the employer sees a duplicate application. Grip hit this — in the
    pipeline from a scan on 07-25, then pitched by a head-hunter on 07-30.

    Marking one channel ``chosen`` clears the flag. Resolving an ambiguity is a real event (Grip's
    turned out to be a forwarded unsigned email, so there was no competing claim at all), and
    without somewhere to record it the board would nag about a decision already made — which is
    how a warning becomes noise people learn to skip.
    """
    if chosen_route(opp) is not None:
        return False
    channels = opp.get("channels") or []
    return len(routes(opp)) >= 2 or (len(routes(opp)) == 1 and len(channels) >= 2)


def route_label(opp: dict) -> str:
    """How this req reached you, as one short display string.

    Shared by the markdown board and the local dashboard on purpose. Route provenance is what the
    duplicate-submission guard is built on, so two surfaces disagreeing about which route a row
    came through is not a cosmetic bug.
    """
    if route_conflict(opp):
        return "⚠ multi-route"
    chosen = chosen_route(opp)
    if chosen is not None:
        return f"{chosen['channel']}/{chosen.get('name', '')}"
    r = routes(opp)
    return f"{r[0]['channel']}/{r[0].get('name', '')}" if r else "scan"


def record_submission(
    ledger: dict,
    opp_id: str,
    channel: str,
    name: str = "",
    today: str | None = None,
    note: str = "",
    force: bool = False,
) -> dict:
    """Record that the CV actually went out, and refuse a second route unless forced.

    This is the guard, not just a log: once a CV has gone via one route, submitting the same req
    through a different one is the mistake worth blocking by default.
    """
    today = today or today_str()
    if channel not in CHANNELS:
        raise ValueError(f"unknown channel {channel!r} — must be one of {', '.join(CHANNELS)}")
    opp = _find(ledger, opp_id=opp_id)
    if opp is None:
        raise KeyError(f"no opportunity with id {opp_id!r}")
    opp.setdefault("submissions", [])

    key = (channel, name.strip().lower())
    prior = [s for s in opp["submissions"] if (s.get("channel"), (s.get("name") or "").strip().lower()) != key]
    if prior and not force:
        was = "; ".join(f"{s['channel']}{'/' + s['name'] if s.get('name') else ''} on {s['date']}" for s in prior)
        raise ValueError(
            f"{opp_id} was already submitted via {was}. Sending it again through "
            f"{channel}{'/' + name if name else ''} risks a duplicate application or a "
            f"competing agency claim. Resolve the channel first, or pass --force if you are sure."
        )
    if not any((s.get("channel"), (s.get("name") or "").strip().lower()) == key for s in opp["submissions"]):
        opp["submissions"].append({"date": today, "channel": channel, "name": name.strip(), "note": note})
    opp["last_touched"] = today
    return opp


# --- mutations (human decisions) ---------------------------------------------


def set_stage(
    ledger: dict,
    opp_id: str,
    stage: str,
    outcome: str | None = None,
    note: str = "",
    today: str | None = None,
) -> dict:
    """Record a human decision. Appends to ``history`` when an ``outcome`` is given.

    Raises ``ValueError`` on an unknown enum value or an ``outcome`` set without
    ``stage="CLOSED"`` — an outcome is by definition how a pursuit *ended*.
    Raises ``KeyError`` when ``opp_id`` is not in the ledger.
    """
    today = today or today_str()
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r} — must be one of {', '.join(STAGES)}")
    if outcome is not None:
        if outcome not in OUTCOMES:
            raise ValueError(f"unknown outcome {outcome!r} — must be one of {', '.join(OUTCOMES)}")
        if stage != "CLOSED":
            raise ValueError(f"outcome {outcome!r} requires stage CLOSED, got {stage!r}")

    opp = _find(ledger, opp_id=opp_id)
    if opp is None:
        raise KeyError(f"no opportunity with id {opp_id!r}")

    prior_stage = opp.get("stage")
    opp["stage"] = stage
    if outcome is not None:
        opp["outcome"] = outcome
        opp["history"].append({"date": today, "event": outcome, "note": note})
    # The applied date the board never had. `submissions[].date` was the nearest thing and it sat
    # on 3 of 115 rows — none of them the four actually in flight — so "when did I apply?" had no
    # answer.
    #
    # Only the APPLIED transition itself records one. An earlier version fired on any
    # IN_FLIGHT_STAGES entry, which meant a row taken straight to OFFER got an APPLIED event dated
    # the day of the offer — an offer cannot precede an application, so that was a confidently
    # wrong answer. No date is the honest output there, for the same reason ``UNVERIFIED`` is a
    # distinct state from ``OPEN``: say what you know, not what would be tidy.
    #
    # Reopening counts as a new application. A row that was CLOSED and is applied to again is a
    # fresh submission, and reporting the original date for it would misdate the live pursuit.
    if stage == "APPLIED":
        events = opp.setdefault("events", [])
        if prior_stage == "CLOSED" or not any(e.get("event") == "APPLIED" for e in events):
            events.append({"date": today, "event": "APPLIED", "note": note})
    opp["last_touched"] = today
    return opp


def add_event(ledger: dict, opp_id: str, event: str, note: str = "", today: str | None = None) -> dict:
    """Append a dated interview round. Returns the row.

    Deliberately **not** ``history``: see :data:`EVENTS` for why conflating the two would send
    every sibling req at the company to WATCH. Append-only — a round that happened stays on the
    record even if the pursuit later dies.
    """
    if event not in EVENTS:
        raise ValueError(f"unknown event {event!r} — must be one of {', '.join(EVENTS)}")
    opp = _find(ledger, opp_id=opp_id)
    if opp is None:
        raise KeyError(f"no opportunity with id {opp_id!r}")
    today = today or today_str()
    opp.setdefault("events", []).append({"date": today, "event": event, "note": note})
    opp["last_touched"] = today
    return opp


def add_risk(ledger: dict, opp_id: str, text: str, source: str = "", today: str | None = None) -> dict:
    """Append a "watch for" note — something to probe or weigh before committing. Returns the row.

    Row-local by design. A risk is a judgment about *this* req, so unlike an outcome it never
    reaches :func:`prior_outcomes` and never reclassifies a sibling. It does count as human
    action for :func:`_is_untouched`, because a scoring change has no business deleting it.
    """
    if not text.strip():
        raise ValueError("risk text is empty — a risk with no text tells you nothing")
    opp = _find(ledger, opp_id=opp_id)
    if opp is None:
        raise KeyError(f"no opportunity with id {opp_id!r}")
    today = today or today_str()
    opp.setdefault("risks", []).append({"date": today, "text": text.strip(), "source": source})
    opp["last_touched"] = today
    return opp


def applied_date(opp: dict) -> str | None:
    """When the CV last went out for the *current* pursuit, or ``None`` if it never did.

    The **latest** ``APPLIED`` event, not the earliest: a req that was closed and applied to again
    has two, and the live pursuit is dated by the second. ``None`` is a real answer — a row that
    reached ``IN_PROCESS`` without an APPLIED transition genuinely has no recorded application date,
    and inventing one would be the lie this field exists to remove.
    """
    dates = [e.get("date", "") for e in opp.get("events") or [] if e.get("event") == "APPLIED"]
    return max((d for d in dates if d), default=None)


def set_liveness(ledger: dict, opp_id: str, status: str, note: str = "", today: str | None = None) -> dict:
    """Record what a *person* saw when they opened the posting. Returns the row.

    Deliberately narrow, mirroring :func:`~src.jobcore.track_sweep.sweep`: this writes
    ``posting_live`` and a ``posting_checked`` receipt, and **nothing else**. Three things it
    must not do, each for a reason that has already bitten:

    * **No history entry.** :func:`bucket` sends a row to WATCH on *any* sibling history, so
      logging a link check as history would drag every other req at that company into WATCH.
      Checking a URL is a market observation, not an outcome.
    * **No stage or outcome change.** A dead posting never closes a pursuit — that separation is
      why an ``IN_PROCESS`` role with a ``CLOSED`` posting reads as normal rather than as an alarm.
    * **No effect on prunability.** A link check is not a decision, so it must not make an
      otherwise-untouched row immune to :func:`prune`.

    The receipt exists because a probe cannot confirm a JS-rendered apply page — 63 of 110 rows
    sit at ``UNVERIFIED`` for that reason alone. Without a durable record of the human check, the
    next sweep would overwrite it and the board would ask for the same eyeball forever.
    """
    if status not in LIVENESS:
        raise ValueError(f"status {status!r} is not one of {LIVENESS}")
    opp = _find(ledger, opp_id=opp_id)
    if opp is None:
        raise KeyError(f"no opportunity with id {opp_id!r}")

    opp["posting_live"] = status
    opp["posting_checked"] = {
        "status": status,
        "date": today or today_str(),
        "by": "human",
        "note": note,
    }
    return opp


def set_policy(ledger: dict, company_slug: str, policy: str, reason: str, today: str | None = None) -> None:
    """Set a company-wide policy (currently only ``do_not_pursue``). Human-only."""
    ledger["company_policy"][company_slug] = {
        "policy": policy,
        "reason": reason,
        "set": today or today_str(),
    }


def is_suppressed(ledger: dict, opp: dict) -> bool:
    """True when the opportunity's company carries a ``do_not_pursue`` policy."""
    pol = ledger["company_policy"].get(opp.get("company_slug", ""))
    return bool(pol and pol.get("policy") == "do_not_pursue")


def prior_outcomes(ledger: dict, company_slug: str, exclude_id: str = "") -> list[dict]:
    """History events from *sibling* rows at the same company.

    This is what lets a genuinely different req at a company that rejected you surface with its
    history attached, instead of either vanishing or reappearing as if nothing happened.
    """
    out: list[dict] = []
    for o in ledger["opportunities"]:
        if o.get("company_slug") != company_slug or o.get("id") == exclude_id:
            continue
        out.extend(o.get("history") or [])
    return out


# --- validation --------------------------------------------------------------


def validate(ledger: dict) -> list[str]:
    """Return human-readable problems; empty list means clean."""
    problems: list[str] = []
    seen_ids: set[str] = set()
    seen_canon: dict[tuple[str, str], str] = {}
    for o in ledger.get("opportunities", []):
        ident = o.get("id", "<no id>")
        for key in _REQUIRED_KEYS:
            if key not in o:
                problems.append(f"{ident}: missing required key {key!r}")
        if ident in seen_ids:
            problems.append(f"{ident}: duplicate id")
        seen_ids.add(ident)
        # Two ids, one req — the punctuation fork. Caught here as well as prevented in
        # _find, because a ledger can already contain a pair from before the fix.
        ck = _row_canon_key(o)
        if all(ck) and ck in seen_canon:
            problems.append(f"{ident}: duplicate role — same req as {seen_canon[ck]!r}; run `pipeline dedupe`")
        else:
            seen_canon[ck] = ident
        if o.get("stage") not in STAGES:
            problems.append(f"{ident}: invalid stage {o.get('stage')!r}")
        outcome = o.get("outcome")
        if outcome is not None:
            if outcome not in OUTCOMES:
                problems.append(f"{ident}: invalid outcome {outcome!r}")
            elif o.get("stage") != "CLOSED":
                problems.append(f"{ident}: outcome {outcome!r} set while stage is {o.get('stage')!r} (needs CLOSED)")
        if o.get("posting_live") not in LIVENESS:
            problems.append(f"{ident}: invalid posting_live {o.get('posting_live')!r}")
        research_depth = o.get("research_depth")
        if research_depth is not None and research_depth not in RESEARCH_DEPTHS:
            problems.append(f"{ident}: invalid research_depth {research_depth!r}")
        checked = o.get("posting_checked")
        if checked is not None:
            if not isinstance(checked, dict):
                problems.append(f"{ident}: posting_checked must be an object")
            elif checked.get("status") not in LIVENESS:
                problems.append(f"{ident}: posting_checked has invalid status {checked.get('status')!r}")
            elif _days_between(checked.get("date", ""), today_str()) is None:
                problems.append(f"{ident}: posting_checked has an unparseable date {checked.get('date')!r}")
        for c in o.get("channels") or []:
            if c.get("channel") not in CHANNELS:
                problems.append(f"{ident}: invalid channel {c.get('channel')!r}")
            elif c["channel"] in ATTRIBUTED_CHANNELS and not (c.get("name") or "").strip():
                problems.append(f"{ident}: channel {c['channel']!r} has no name — cannot de-duplicate the route")
        # `events` and `risks` post-date the 115 committed rows, so absence is normal and clean —
        # only a malformed entry is a problem.
        for e in o.get("events") or []:
            if e.get("event") not in EVENTS:
                problems.append(f"{ident}: invalid event {e.get('event')!r}")
            elif _days_between(e.get("date", ""), today_str()) is None:
                problems.append(f"{ident}: unparseable event date {e.get('date')!r}")
        for r in o.get("risks") or []:
            if not (r.get("text") or "").strip():
                problems.append(f"{ident}: risk with no text")
            elif _days_between(r.get("date", ""), today_str()) is None:
                problems.append(f"{ident}: unparseable risk date {r.get('date')!r}")
        for s in o.get("submissions") or []:
            if s.get("channel") not in CHANNELS:
                problems.append(f"{ident}: invalid submission channel {s.get('channel')!r}")
        distinct_sub_routes = {(s.get("channel"), (s.get("name") or "").lower()) for s in o.get("submissions") or []}
        if len(distinct_sub_routes) > 1:
            problems.append(
                f"{ident}: submitted via {len(distinct_sub_routes)} different routes — "
                f"duplicate-application risk, confirm this was intended"
            )
    return problems


# --- prune -------------------------------------------------------------------


def _is_untouched(opp: dict) -> bool:
    """True when nobody has acted on this row, so removing it destroys no decision.

    Deliberately strict: a person-attributed channel counts as "touched" even with no
    submission, because a head-hunter naming a req is itself information a scoring change
    has no business deleting.
    """
    if opp.get("stage") != "NEW" or opp.get("outcome"):
        return False
    if opp.get("history") or opp.get("submissions") or opp.get("position_folder"):
        return False
    # An interview round or a recorded risk is human work too. Row-local, unlike history — see
    # :data:`EVENTS` — but just as undeletable by a scoring change.
    if opp.get("events") or opp.get("risks"):
        return False
    return not any(c.get("channel") in ATTRIBUTED_CHANNELS for c in opp.get("channels") or [])


def prune(ledger: dict) -> int:
    """Re-apply the relevance gate to untouched rows. Returns rows removed.

    Exists because :func:`~src.jobcore.scoring.is_relevant` runs at intake only. When the
    gates were widened on 2026-08-05 (business-function and hardware-design titles, and a
    domain check that had silently stopped firing on Comeet's compound labels), the 34 rows
    the same day's poll had already inserted were unreachable — the fix protected the future
    and left the board exactly as noisy as before.

    Untouched-only, by :func:`_is_untouched`: a scoring change must never be able to delete
    a pursuit. Anything a human or a named route touched stays and is reported instead.
    """
    doomed = [
        o
        for o in ledger.get("opportunities", [])
        if _is_untouched(o) and not is_relevant({"title": o.get("role") or "", "domain": o.get("domain") or ""})
    ]
    for o in doomed:
        ledger["opportunities"].remove(o)
    return len(doomed)


# --- route opened, nothing sent ----------------------------------------------

# How long a person's introduction may sit unsent before the board says so. Two days,
# because the cost is measured in funnel position, not in politeness: a role pitched by a
# head-hunter with the CV already built and gated can lose its slot within a week to a
# candidate sourced elsewhere, and nothing on the board tracked the gap.
UNSENT_GRACE_DAYS = 2

# How long a hand-check counts as evidence about a posting. Two weeks: long enough that
# verifying a link is worth doing, short enough that a role you eyeballed last month is not
# still presented as confirmed. A 9/10 Amdocs role sat at the top of ACT NOW on 2026-07-30
# while the posting had already closed — staleness is the failure mode this bounds.
CHECK_FRESH_DAYS = 14


def _days_between(earlier: str, later: str) -> int | None:
    """Whole days between two ISO dates, or None if either is unparseable."""
    try:
        return (date.fromisoformat(later) - date.fromisoformat(earlier)).days
    except (TypeError, ValueError):
        return None


def unsent_person_routes(
    ledger: dict, today: str | None = None, grace_days: int = UNSENT_GRACE_DAYS
) -> list[tuple[dict, int]]:
    """Rows a *person* introduced that still carry no submission. ``[(opp, age_days)]``.

    Scoped to :data:`ATTRIBUTED_CHANNELS` on purpose: a scan finding a public posting
    creates no obligation and no decaying advantage, but a named head-hunter, inbound
    recruiter or friend handing over a req does both. ``CLOSED`` rows and suppressed
    companies are excluded — a decision already taken is not a pending action.

    Oldest first, because that is the order in which the advantage is being lost.
    """
    today = today or today_str()
    out: list[tuple[dict, int]] = []
    for o in ledger.get("opportunities", []):
        if o.get("stage") == "CLOSED" or is_suppressed(ledger, o):
            continue
        if o.get("submissions"):
            continue
        dates = [
            c.get("date")
            for c in (o.get("channels") or [])
            if c.get("channel") in ATTRIBUTED_CHANNELS and c.get("date")
        ]
        if not dates:
            continue
        age = _days_between(min(dates), today)
        if age is not None and age >= grace_days:
            out.append((o, age))
    return sorted(out, key=lambda pair: -pair[1])


# --- dedupe ------------------------------------------------------------------

# Field groups that decide which of a forked pair survives. Human state is anything a
# person or a sweep decided; tool state is anything the next poll will rewrite anyway.
_HUMAN_STATE = ("stage", "outcome", "history", "channels", "submissions", "position_folder", "events", "risks")
_STAGE_RANK = {s: i for i, s in enumerate(STAGES)}


def _human_weight(opp: dict) -> tuple:
    """How much irreplaceable state a row carries. Higher wins a merge."""
    return (
        _STAGE_RANK.get(opp.get("stage") or "NEW", 0),
        len(opp.get("submissions") or []),
        len(opp.get("history") or []),
        len(opp.get("events") or []),
        len(opp.get("risks") or []),
        len(opp.get("channels") or []),
        1 if opp.get("position_folder") else 0,
        1 if opp.get("outcome") else 0,
    )


def dedupe(ledger: dict) -> int:
    """Merge rows that are the same req under different ids. Returns rows removed.

    Keeps the row carrying the most human state (furthest stage, then submissions /
    history / events / risks / channels / folder) and folds the loser's channels, submissions,
    history, events and risks into it, so a merge can never silently drop a recorded route — that
    is the one thing the route guard depends on. Tool-owned fields (url, score, location, …) are left on
    the winner; the next poll refreshes them regardless.
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for o in ledger.get("opportunities", []):
        ck = _row_canon_key(o)
        if all(ck):
            groups.setdefault(ck, []).append(o)

    removed = 0
    for rows in groups.values():
        if len(rows) < 2:
            continue
        rows.sort(key=_human_weight, reverse=True)
        winner, losers = rows[0], rows[1:]
        for loser in losers:
            for field in ("channels", "submissions", "history", "events", "risks"):
                merged = list(winner.get(field) or [])
                seen = {json.dumps(x, sort_keys=True, ensure_ascii=False) for x in merged}
                for item in loser.get(field) or []:
                    fp = json.dumps(item, sort_keys=True, ensure_ascii=False)
                    if fp not in seen:
                        merged.append(item)
                        seen.add(fp)
                winner[field] = merged
            for field in _HUMAN_STATE:
                if not winner.get(field) and loser.get(field):
                    winner[field] = loser[field]
            if loser.get("first_seen") and (not winner.get("first_seen") or loser["first_seen"] < winner["first_seen"]):
                winner["first_seen"] = loser["first_seen"]
            ledger["opportunities"].remove(loser)
            removed += 1
    return removed


# --- bucketing / render ------------------------------------------------------


def needs_eyeball(opp: dict, today: str | None = None) -> bool:
    """True when this posting's status cannot be established by any automated check.

    Either the host renders its status client-side (:data:`JS_RENDERED_HOSTS`) or the posting has
    simply never come back verified. Such a row may still be worth pursuing — but it must never be
    presented as a confirmed-open opportunity. A 9/10 Amdocs role sat at the top of ACT NOW while
    the posting had in fact closed, precisely because "unverified" was rendered indistinguishable
    from "verified open".

    ``LISTED`` counts as established: the req came back from the board's own JSON API in a
    poll, which is stronger evidence than fetching a client-rendered apply page, even though
    the page itself stays unreadable.

    A **recent hand-check settles it outright**, whatever the host. On
    :data:`JS_RENDERED_HOSTS` no automated result can ever clear this flag, so without that a
    person could open the page, read it, record the verdict — and still be told to go and check.
    It expires after :data:`CHECK_FRESH_DAYS`, because a verdict from months ago is not evidence
    about today's posting.
    """
    checked = opp.get("posting_checked") or {}
    age = _days_between(checked.get("date", ""), today or today_str())
    if age is not None and 0 <= age <= CHECK_FRESH_DAYS:
        return False

    url = (opp.get("url") or "").lower()
    if any(h in url for h in JS_RENDERED_HOSTS):
        return True
    return opp.get("posting_live") not in ("OPEN", "LISTED")


def _sort_key(o: dict) -> tuple:
    """Rank by score, but break ties toward postings we actually confirmed are open.

    Verification is a tiebreak, not an override: a confirmed-open 7/10 should not outrank an
    unverified 9/10, because the 9/10 may well be live. It only stops the unverified row from
    silently taking the top slot when something confirmed is equally good.
    """
    return (-int(o.get("score") or 0), needs_eyeball(o), o.get("company", ""))


#: Title words that mean the same seat but are spelled differently across boards. Applied
#: token-by-token, never as a substring, so "Leadership" stays a distinct word.
_ROLE_SYNONYMS = {"leader": "lead", "leaders": "lead", "mgr": "manager"}


def _norm_role(role: str) -> str:
    """Role → a comparison key stable across re-titling ("R&D Team Leader" ≡ "rd team lead").

    Punctuation is **deleted**, not turned into a space, so "R&D" collapses to "rd" —
    matching what ``slugify`` already does for row ids (``acme:rd-team-leader``).
    Substituting a space instead would key it as "r d" and silently never match.
    Whitespace is preserved as a separator, so "Backend TL — Platform" keeps its words.

    "Leader" folds to "lead" because boards spell the same seat both ways, and that one
    letter let a *rejected* req re-enter as new: Aidoc rejected "AI Software Team **Leader**,
    ML Platform" on 2026-07-30 and the 08-05 poll filed "AI Software Team **Lead**, ML
    Platform" as a clean 8/10 `NEW` row. Same defect class as the `lead\\b` regex bug in
    ``scoring.EXCLUDE_TITLES`` — one spelling of a word standing in for both.
    """
    kept = "".join(c for c in (role or "").lower() if c.isalnum() or c.isspace())
    return " ".join(_ROLE_SYNONYMS.get(w, w) for w in kept.split())


def _digest(value: str) -> str:
    """Short sha256 of a match key, for the obfuscated export."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def suppress_list(ledger: dict, hashed: bool = False) -> dict:
    """The rows a downstream scanner must not re-surface as new.

    Exists because the ledger forked. This repo holds the outcomes (a rejection, a
    decline, a do-not-pursue policy); a second scanner in another repo holds none of
    them, so its delta run keeps re-proposing roles that are already dead — which is
    exactly what happened on 2026-08-03. Rather than let a second durable ledger
    drift, this exports a **one-directional** suppression feed: this ledger stays
    master, the scanner is a consumer that never writes back.

    Three match keys per row, strongest first, because a scanner may see a re-titled
    req or a moved URL: ``job_id``, ``url``, and ``(company_slug, normalized role)``.

    ``in_flight`` is reported separately and deliberately **not** merged into the
    suppression set — an active pursuit should be *marked*, never dropped, or a live
    interview process would vanish from the scan.

    On ``hashed``: it replaces company/role plaintext with short digests so the file
    can live in a repo that forbids pursuit-company data. Be clear-eyed about what
    that buys — the input space is small and guessable, so a consumer holding a
    candidate company list can still confirm membership by hashing its own
    candidates. That is in fact how the filter works. It prevents a *reader* of the
    file from learning the pursuit list; it is obfuscation, not confidentiality. For
    real separation, keep the plaintext file in this private repo and point the
    scanner at it by absolute path instead of copying it across.
    """
    policy = ledger.get("company_policy", {}) or {}
    dnp = sorted(s for s, p in policy.items() if (p or {}).get("policy") == "do_not_pursue")

    def entry(o: dict) -> dict:
        slug, role = o.get("company_slug", ""), o.get("role", "")
        row = {
            "job_id": o.get("job_id", "") or "",
            "url": o.get("url", "") or "",
            "role_key": f"{slug}|{_norm_role(role)}",
            "outcome": o.get("outcome") or "",
            "last_touched": o.get("last_touched", ""),
        }
        if hashed:
            row["role_key"] = _digest(row["role_key"])
            row["job_id"] = _digest(row["job_id"]) if row["job_id"] else ""
            row["url"] = _digest(row["url"]) if row["url"] else ""
        else:
            row["company"] = o.get("company", "")
            row["role"] = role
        return row

    opps = ledger.get("opportunities", [])
    return {
        "schema": "suppress/1",
        "generated": today_str(),
        "hashed": bool(hashed),
        "source": "claude-test:job-tracking/pipeline.json",
        "do_not_pursue": [_digest(s) for s in dnp] if hashed else dnp,
        "closed": [entry(o) for o in opps if o.get("stage") == "CLOSED"],
        "in_flight": [entry(o) for o in opps if o.get("stage") in IN_FLIGHT_STAGES],
    }


def bucket(ledger: dict) -> dict[str, list[dict]]:
    """Route every row into exactly one board section.

    Order matters: a ``do_not_pursue`` company wins over everything, then closed pursuits, then
    in-flight ones, then altitude. ACT NOW is deliberately the narrowest bucket — it should only
    ever hold roles that are in-band, confirmed open, and not yet started.
    """
    out: dict[str, list[dict]] = {
        "act_now": [],
        "in_flight": [],
        "watch": [],
        "above_band": [],
        "closed": [],
        "suppressed": [],
    }
    for o in ledger.get("opportunities", []):
        if is_suppressed(ledger, o):
            out["suppressed"].append(o)
        elif o.get("stage") == "CLOSED":
            out["closed"].append(o)
        elif o.get("stage") in IN_FLIGHT_STAGES:
            out["in_flight"].append(o)
        elif o.get("level_band") == "above_band":
            out["above_band"].append(o)
        elif o.get("posting_live") == "CLOSED" or prior_outcomes(ledger, o.get("company_slug", ""), o.get("id", "")):
            # WATCH is for things that need a judgement call before you invest: the posting died,
            # or this company has already rejected/declined you on a different req.
            # Note UNVERIFIED does NOT land here — LinkedIn guest liveness can almost never
            # confirm OPEN, so treating unverified as suspicious emptied ACT NOW entirely. It is
            # surfaced as a per-row flag instead.
            out["watch"].append(o)
        else:
            out["act_now"].append(o)

    for key in ("act_now", "watch", "above_band", "in_flight"):
        out[key].sort(key=_sort_key)
    out["closed"].sort(key=lambda o: o.get("company", ""))
    out["suppressed"].sort(key=lambda o: o.get("company", ""))
    return out


def _progress_note(opp: dict) -> str:
    """How far along a pursuit is, for one markdown line: applied date, last round, risk count.

    The board and the HTML panel were only ever required to agree on *classification*, but leaving
    these browser-only meant "when did I apply?" — the question ``events`` was added to answer — had
    no answer in the artifact most people actually read.
    """
    bits: list[str] = []
    applied = applied_date(opp)
    if applied:
        bits.append(f"applied {applied}")
    rounds = [e for e in opp.get("events") or [] if e.get("event") != "APPLIED"]
    if rounds:
        last = rounds[-1]
        bits.append(f"last: {last.get('event')} {last.get('date')}")
    risks = opp.get("risks") or []
    if risks:
        bits.append(f"⚠ {len(risks)} watch-for")
    if opp.get("depth_risk"):
        bits.append("🧭 depth advisory")
    return (" · " + " · ".join(bits)) if bits else ""


def render(ledger: dict, today: str | None = None) -> str:
    """Render the board — one ranked answer to 'where does my next hour go?'."""
    today = today or today_str()
    b = bucket(ledger)
    L: list[str] = [f"# Pipeline Board — {today}", ""]
    L.append(
        f"_Generated from `job-tracking/pipeline.json` — do not hand-edit this file. "
        f"{len(ledger.get('opportunities', []))} opportunities tracked._"
    )
    L.append("")

    conflicted = [
        o
        for o in ledger.get("opportunities", [])
        if route_conflict(o) and not is_suppressed(ledger, o) and o.get("stage") != "CLOSED"
    ]
    if conflicted:
        L += [
            "## 🔴 RESOLVE THE ROUTE FIRST — the same req reached you more than one way",
            "",
            "_Send your CV through **one** of these only. Two routes means two agencies can claim "
            "the introduction, or the employer sees a duplicate application._",
            "",
        ]
        for o in conflicted:
            routes_txt = " · ".join(
                f"{c['channel']}{'/' + c['name'] if c.get('name') else ''} ({c['date']})"
                for c in (o.get("channels") or [])
            )
            sent = o.get("submissions") or []
            sent_txt = (
                f" — ⚠ ALREADY SENT via {sent[0]['channel']}"
                f"{'/' + sent[0]['name'] if sent[0].get('name') else ''} on {sent[0]['date']}"
                if sent
                else " — not yet sent"
            )
            L.append(f"- **{o['company']}** — {o['role']}: {routes_txt}{sent_txt}")
        L.append("")

    unsent = unsent_person_routes(ledger, today)
    if unsent:
        L += [
            "## 🟠 ROUTE OPENED, NOTHING SENT — a person handed you this and no CV went out",
            "",
            "_A scan finding a posting costs you nothing to sit on. A named introduction decays: "
            "a role pitched by name, with the CV already built and gated, can lose its slot within "
            "days to a candidate sourced elsewhere. Send it or close it — leaving it open is "
            "the one option that loses by default._",
            "",
        ]
        for o, age in unsent:
            routes_txt = " · ".join(
                f"{c['channel']}{'/' + c['name'] if c.get('name') else ''} ({c['date']})"
                for c in (o.get("channels") or [])
                if c.get("channel") in ATTRIBUTED_CHANNELS
            )
            L.append(f"- **{age} days** — **{o['company']}** — {o['role']}: {routes_txt} · stage `{o.get('stage')}`")
        L.append("")

    L += ["## 🎯 ACT NOW — tailor a CV / ask for a referral", ""]
    if b["act_now"]:
        L += [
            "| Score | Company | Role | Route | Posted | Posting | URL |",
            "|-------|---------|------|-------|--------|---------|-----|",
        ]
        for o in b["act_now"][:ACT_NOW_LIMIT]:
            route_txt = route_label(o)
            if o.get("posting_live") == "OPEN":
                live_txt = "✅ OPEN"
            elif o.get("posting_live") == "LISTED":
                live_txt = "📋 LISTED on the board's API"
            elif any(h in (o.get("url") or "").lower() for h in JS_RENDERED_HOSTS):
                live_txt = "👁 OPEN THE LINK (status is JS-rendered — no checker can read it)"
            else:
                live_txt = "❓ unconfirmed — open the link"
            L.append(
                f"| {o.get('score')}/10 | {o['company']} | {o['role']} | {route_txt} "
                f"| {o.get('posted_date') or '?'} | {live_txt} | {o.get('url', '')} |"
            )
        L += [
            "",
            "_`✅ OPEN` = the apply URL was fetched and confirmed. `📋 LISTED` = the req came back "
            "from the board's own JSON API in the last poll, so it is in that board's current "
            "listing — its apply page is client-rendered and unreadable by any HTTP checker, which "
            "is not the same as doubtful. Anything else is genuinely **unconfirmed** — open the link "
            "before you invest an hour in tailoring. A 9/10 Amdocs role sat at the top of this list "
            "on 2026-07-30 while the posting had already closed._",
        ]
        if len(b["act_now"]) > ACT_NOW_LIMIT:
            L += [
                "",
                f"_+{len(b['act_now']) - ACT_NOW_LIMIT} more un-started in-band roles below "
                f"{b['act_now'][ACT_NOW_LIMIT]['score']}/10 — see `job-tracking/pipeline.json`._",
            ]
    else:
        L.append("_Nothing queued — every in-band open role is already in flight or on watch._")
    L.append("")

    # Recorded risks on rows you are about to spend an hour on. Kept out of the table above so its
    # columns stay stable, but not browser-only: a watch-for that nobody re-reads before an
    # interview is a note that did no work.
    flagged = [o for o in b["act_now"][:ACT_NOW_LIMIT] if o.get("risks")]
    if flagged:
        L += ["**⚠ Watch for, on the queue above:**", ""]
        for o in flagged:
            for r in o["risks"]:
                src = f" ({r['source']})" if r.get("source") else ""
                L.append(f"- **{o['company']}** — {r.get('text', '')}{src}")
        L.append("")

    # Advisory only — never removes a row from ACT NOW or changes its score. Flags titles naming
    # titles the candidate listed under ``roles.depth_risk_titles`` — areas where a round may
    # probe depth their CV is thinner on than their main strength. See scoring.depth_risk_flag.
    depth_flagged = [o for o in b["act_now"][:ACT_NOW_LIMIT] if o.get("depth_risk")]
    if depth_flagged:
        L += [
            "**🧭 Depth advisory, on the queue above:** the title names an area you flagged in "
            "your profile (`roles.depth_risk_titles`) as thinner on your CV — weight interview "
            "prep toward that depth, not only your main strength.",
            "",
        ]
        L += [f"- {o['company']} — {o['role']}" for o in depth_flagged]
        L.append("")

    L += ["## ⏳ IN FLIGHT", ""]
    if b["in_flight"]:
        L += [
            f"- **{o['company']}** — {o['role']} · `{o['stage']}` · posting {o.get('posting_live')}{_progress_note(o)}"
            for o in b["in_flight"]
        ]
    else:
        L.append("_None._")
    L.append("")

    L += ["## 👀 WATCH — history attached / unconfirmed posting", ""]
    if b["watch"]:
        for o in b["watch"]:
            prior = prior_outcomes(ledger, o.get("company_slug", ""), o.get("id", ""))
            why = f"posting {o.get('posting_live')}"
            if o.get("depth_risk"):
                why += " · 🧭 depth advisory"
            if prior:
                last = prior[-1]
                why += f" · ⚠ prior at this company: {last['event']} {last['date']}"
                if last.get("note"):
                    why += f" ({last['note']})"
            L.append(f"- {o.get('score')}/10 **{o['company']}** — {o['role']} · {why}")
    else:
        L.append("_None._")
    L.append("")

    L += [f"## 🔻 ABOVE BAND — collapsed ({len(b['above_band'])})", ""]
    if b["above_band"]:
        L.append("_Director/VP/head-of titles: a rung above the target band. Kept, not dropped._")
        L += [f"- {o['company']} — {o['role']}" for o in b["above_band"]]
    else:
        L.append("_None._")
    L.append("")

    tally = ", ".join(
        f"{sum(1 for o in b['closed'] if o.get('outcome') == oc)} {oc.lower().replace('_', '-')}" for oc in OUTCOMES
    )
    L += [f"## 📁 CLOSED — collapsed ({len(b['closed'])}: {tally})", ""]
    if b["closed"]:
        L += [f"- {o['company']} — {o['role']} · {o.get('outcome') or 'no outcome recorded'}" for o in b["closed"]]
    else:
        L.append("_None._")
    L.append("")

    L += [f"## 🚫 SUPPRESSED — {len(b['suppressed'])} by company policy", ""]
    if b["suppressed"]:
        for o in b["suppressed"]:
            pol = ledger["company_policy"].get(o.get("company_slug", ""), {})
            L.append(f"- {o['company']} — {o['role']} · {pol.get('reason', 'do_not_pursue')}")
    else:
        L.append("_None._")

    return "\n".join(L) + "\n"


# --- CLI ---------------------------------------------------------------------


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--ledger", default=str(LEDGER_DEFAULT), help="Ledger JSON path")
    p.add_argument("--today", default=None, help="Override today's date (YYYY-MM-DD, profile timezone)")


def main(argv: list[str] | None = None) -> int:
    force_utf8_output()
    ap = argparse.ArgumentParser(description="Decision-aware job pipeline ledger.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_up = sub.add_parser("upsert", help="Merge rows from a shortlist JSON into the ledger")
    p_up.add_argument("--from", dest="src", required=True, help="Shortlist JSON (list of rows)")
    _add_common(p_up)

    p_add = sub.add_parser("add", help="Add a single opportunity by hand")
    p_add.add_argument("--company", required=True)
    p_add.add_argument("--role", required=True)
    p_add.add_argument("--url", required=True)
    p_add.add_argument("--score", type=int, default=5)
    p_add.add_argument("--location", default="")
    p_add.add_argument("--source", default="manual")
    p_add.add_argument("--job-id", dest="job_id", default="")
    p_add.add_argument("--folder", default=None, help="positions/<slug> if one exists")
    p_add.add_argument(
        "--channel",
        default="manual",
        choices=CHANNELS,
        help="How this reached you (default: manual). Use head_hunter/friend_referral etc. with --name.",
    )
    p_add.add_argument("--name", default="", help="Who brought it — required for head_hunter/recruiter/friend")
    _add_common(p_add)

    p_set = sub.add_parser("set", help="Record a stage/outcome decision")
    p_set.add_argument("opp_id")
    p_set.add_argument("--stage", required=True, choices=STAGES)
    p_set.add_argument("--outcome", default=None, choices=OUTCOMES)
    p_set.add_argument("--note", default="")
    p_set.add_argument("--folder", default=None, help="Attach a positions/<slug> folder")
    p_set.add_argument(
        "--research-depth",
        default=None,
        choices=RESEARCH_DEPTHS,
        dest="research_depth",
        help="Record that company-researcher/interview-prep completed a deep pass",
    )
    _add_common(p_set)

    p_chan = sub.add_parser("channel", help="Record HOW an opportunity reached you (provenance)")
    p_chan.add_argument("opp_id")
    p_chan.add_argument("--channel", required=True, choices=CHANNELS)
    p_chan.add_argument("--name", default="", help="Who (head-hunter / recruiter / friend) — required for those")
    p_chan.add_argument("--note", default="")
    p_chan.add_argument(
        "--chosen", action="store_true", help="Mark this as THE route to use (resolves a multi-route warning)"
    )
    _add_common(p_chan)

    p_sub = sub.add_parser("submit", help="Record that the CV went out; blocks a second route")
    p_sub.add_argument("opp_id")
    p_sub.add_argument("--channel", required=True, choices=CHANNELS)
    p_sub.add_argument("--name", default="")
    p_sub.add_argument("--note", default="")
    p_sub.add_argument("--force", action="store_true", help="Submit anyway despite an existing route")
    _add_common(p_sub)

    p_liv = sub.add_parser("liveness", help="Record what you saw when you opened the posting yourself")
    p_liv.add_argument("id", help="opportunity id")
    p_liv.add_argument("--status", required=True, choices=list(LIVENESS))
    p_liv.add_argument("--note", default="", help="what you saw, in your own words")
    _add_common(p_liv)

    p_evt = sub.add_parser("event", help="Record a dated interview round (separate from stage/outcome)")
    p_evt.add_argument("opp_id")
    p_evt.add_argument("--type", dest="event", required=True, choices=list(EVENTS))
    p_evt.add_argument("--note", default="", help="who was in the room, how it went")
    _add_common(p_evt)

    p_risk = sub.add_parser("risk", help="Record a 'watch for' — something to probe before committing")
    p_risk.add_argument("opp_id")
    p_risk.add_argument("--text", required=True, help="the risk, in your own words")
    p_risk.add_argument("--source", default="", help="where it came from (glassdoor, head-hunter, the JD)")
    _add_common(p_risk)

    p_pol = sub.add_parser("policy", help="Set a company-wide policy")
    p_pol.add_argument("company_slug")
    p_pol.add_argument("--do-not-pursue", action="store_true", required=True)
    p_pol.add_argument("--reason", required=True)
    _add_common(p_pol)

    p_ren = sub.add_parser("render", help="Write the markdown board")
    p_ren.add_argument("--out", default=str(BOARD_DEFAULT))
    _add_common(p_ren)

    p_val = sub.add_parser("validate", help="Check the ledger for schema problems")
    _add_common(p_val)

    p_ded = sub.add_parser("dedupe", help="Merge rows that are the same req under two ids")
    p_ded.add_argument("--apply", action="store_true", help="write the merge (default is a dry run that only reports)")
    _add_common(p_ded)

    p_pru = sub.add_parser("prune", help="Re-apply the relevance gate to untouched rows")
    p_pru.add_argument("--apply", action="store_true", help="write the removal (default is a dry run)")
    _add_common(p_pru)

    p_exp = sub.add_parser(
        "export-suppress",
        help="Emit the closed/do-not-pursue feed so a downstream scanner stops re-surfacing dead roles",
    )
    p_exp.add_argument("--out", default="-", help="output path, or - for stdout (default)")
    p_exp.add_argument(
        "--hashed",
        action="store_true",
        help="digest company/role instead of plaintext, for a repo that forbids pursuit-company data "
        "(obfuscation, not confidentiality — see suppress_list docs)",
    )
    _add_common(p_exp)

    args = ap.parse_args(argv)
    day = args.today or today_str()
    ledger = load(args.ledger)

    if args.cmd == "upsert":
        rows = json.loads(Path(args.src).read_text(encoding="utf-8"))
        ins, upd = upsert(ledger, rows, day)
        save(ledger, args.ledger)
        print(f"pipeline: +{ins} new / {upd} updated", file=sys.stderr)

    elif args.cmd == "add":
        # Pass the real channel — defaulting to upsert's `ats_scan` stamped hand-added rows with
        # provenance they never had, which defeats the whole point of tracking where a role
        # came from.
        try:
            ins, upd = upsert(
                ledger,
                [
                    {
                        "company": args.company,
                        "role": args.role,
                        "url": args.url,
                        "score": args.score,
                        "location": args.location,
                        "source": args.source,
                        "job_id": args.job_id,
                    }
                ],
                day,
                channel="",  # added explicitly below so a bad --name fails before anything is saved
            )
            add_channel(ledger, make_id(args.company, args.role), args.channel, args.name, day)
        except (ValueError, KeyError) as e:
            print(f"pipeline: {e}", file=sys.stderr)
            return 1
        if args.folder:
            row = _find(ledger, opp_id=make_id(args.company, args.role))
            if row is not None:
                row["position_folder"] = args.folder
        save(ledger, args.ledger)
        via = f"{args.channel}{'/' + args.name if args.name else ''}"
        print(f"pipeline: +{ins} new / {upd} updated → {make_id(args.company, args.role)} via {via}", file=sys.stderr)

    elif args.cmd == "set":
        try:
            row = set_stage(ledger, args.opp_id, args.stage, args.outcome, args.note, day)
        except (ValueError, KeyError) as e:
            print(f"pipeline: {e}", file=sys.stderr)
            return 1
        if args.folder:
            row["position_folder"] = args.folder
        if args.research_depth:
            row["research_depth"] = args.research_depth
        save(ledger, args.ledger)
        print(f"pipeline: {args.opp_id} → {args.stage}{' / ' + args.outcome if args.outcome else ''}", file=sys.stderr)

    elif args.cmd == "export-suppress":
        feed = suppress_list(ledger, hashed=args.hashed)
        text = json.dumps(feed, indent=2, ensure_ascii=False)
        if args.out == "-":
            print(text)
        else:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(
            f"pipeline: suppress feed → closed={len(feed['closed'])} "
            f"in-flight={len(feed['in_flight'])} do-not-pursue={len(feed['do_not_pursue'])}"
            f"{' (hashed)' if args.hashed else ''}"
            f"{'' if args.out == '-' else ' → ' + args.out}",
            file=sys.stderr,
        )

    elif args.cmd == "channel":
        try:
            opp = add_channel(ledger, args.opp_id, args.channel, args.name, day, args.note, args.chosen)
        except (ValueError, KeyError) as e:
            print(f"pipeline: {e}", file=sys.stderr)
            return 1
        save(ledger, args.ledger)
        msg = f"pipeline: {args.opp_id} + channel {args.channel}{'/' + args.name if args.name else ''}"
        if args.chosen:
            msg += "  ✅ chosen route — multi-route warning cleared"
        elif route_conflict(opp):
            msg += "  🔴 MULTI-ROUTE — resolve which route to use before sending the CV (--chosen)"
        print(msg, file=sys.stderr)

    elif args.cmd == "submit":
        try:
            record_submission(ledger, args.opp_id, args.channel, args.name, day, args.note, args.force)
        except (ValueError, KeyError) as e:
            print(f"pipeline: {e}", file=sys.stderr)
            return 1
        save(ledger, args.ledger)
        print(
            f"pipeline: {args.opp_id} submitted via {args.channel}{'/' + args.name if args.name else ''} on {day}",
            file=sys.stderr,
        )

    elif args.cmd == "liveness":
        try:
            set_liveness(ledger, args.id, args.status, args.note, day)
        except (ValueError, KeyError) as e:
            print(f"pipeline: {e}", file=sys.stderr)
            return 1
        save(ledger, args.ledger)
        print(f"pipeline: {args.id} posting is {args.status} (checked by hand {day})", file=sys.stderr)

    elif args.cmd == "event":
        try:
            opp = add_event(ledger, args.opp_id, args.event, args.note, day)
        except (ValueError, KeyError) as e:
            print(f"pipeline: {e}", file=sys.stderr)
            return 1
        save(ledger, args.ledger)
        print(f"pipeline: {args.opp_id} + {args.event} on {day}", file=sys.stderr)
        if args.event in INTERVIEW_SIGNAL_EVENTS and opp.get("research_depth", "quick") != "deep":
            print(
                f"pipeline: → research_depth is still 'quick' for {args.opp_id}. Consider running "
                f"deep company-researcher + interview-prep for {opp.get('company', '')}, then: "
                f"uv run python -m src.jobcore.pipeline set {args.opp_id} --stage {opp.get('stage')} "
                f"--research-depth deep",
                file=sys.stderr,
            )

    elif args.cmd == "risk":
        try:
            add_risk(ledger, args.opp_id, args.text, args.source, day)
        except (ValueError, KeyError) as e:
            print(f"pipeline: {e}", file=sys.stderr)
            return 1
        save(ledger, args.ledger)
        print(f"pipeline: {args.opp_id} + risk noted ({args.source or 'no source'})", file=sys.stderr)

    elif args.cmd == "policy":
        set_policy(ledger, args.company_slug, "do_not_pursue", args.reason, day)
        save(ledger, args.ledger)
        print(f"pipeline: {args.company_slug} → do_not_pursue ({args.reason})", file=sys.stderr)

    elif args.cmd == "render":
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(render(ledger, day), encoding="utf-8")
        b = bucket(ledger)
        print(
            f"pipeline: board → {args.out} · act-now={len(b['act_now'])} in-flight={len(b['in_flight'])} "
            f"watch={len(b['watch'])} above-band={len(b['above_band'])} closed={len(b['closed'])} "
            f"suppressed={len(b['suppressed'])}",
            file=sys.stderr,
        )

    elif args.cmd == "dedupe":
        groups: dict[tuple[str, str], list[str]] = {}
        for o in ledger.get("opportunities", []):
            ck = _row_canon_key(o)
            if all(ck):
                groups.setdefault(ck, []).append(o.get("id", "<no id>"))
        forked = {k: v for k, v in groups.items() if len(v) > 1}
        for ids in forked.values():
            print(f"pipeline: forked req — {' + '.join(ids)}", file=sys.stderr)
        if not args.apply:
            print(
                f"pipeline: {len(forked)} forked req(s), 0 merged (dry run — pass --apply to write)",
                file=sys.stderr,
            )
            return 0
        removed = dedupe(ledger)
        save(ledger, args.ledger)
        print(f"pipeline: merged {removed} duplicate row(s) → {args.ledger}", file=sys.stderr)

    elif args.cmd == "prune":
        doomed = [
            o
            for o in ledger.get("opportunities", [])
            if _is_untouched(o) and not is_relevant({"title": o.get("role") or "", "domain": o.get("domain") or ""})
        ]
        for o in doomed:
            print(f"pipeline: irrelevant — {o.get('id')} [{o.get('domain') or 'no domain'}]", file=sys.stderr)
        if not args.apply:
            print(
                f"pipeline: {len(doomed)} untouched row(s) now fail the gate, 0 removed "
                f"(dry run — pass --apply to write)",
                file=sys.stderr,
            )
            return 0
        removed = prune(ledger)
        save(ledger, args.ledger)
        print(f"pipeline: pruned {removed} row(s) → {args.ledger}", file=sys.stderr)

    elif args.cmd == "validate":
        problems = validate(ledger)
        if problems:
            for p in problems:
                print(f"pipeline: {p}", file=sys.stderr)
            return 1
        print(f"pipeline: ledger OK ({len(ledger['opportunities'])} opportunities)", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
