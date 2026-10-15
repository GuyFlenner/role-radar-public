"""Ledger → view model for the local pipeline board.

Pure. No I/O, no HTTP, no clock of its own — hand it a ledger and get back a JSON-serialisable
dict. That is what makes the dashboard testable without a socket.

**This module classifies nothing.** Every bucket, alarm, route and liveness judgement is imported
from :mod:`src.jobcore.pipeline`, which is also what renders ``PIPELINE_BOARD.md``. If the view
re-derived any of it, the page and the markdown board could show different answers to
"where does my next hour go?" — the exact drift ``src/jobcore/`` exists to prevent.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote_plus

from . import company_snapshot as profile_mod
from . import pipeline

#: Where a row with no stored URL sends you. 28 of the 110 real rows carry no link at all, and
#: 63 are UNVERIFIED — the whole point of the board is deciding what is worth an hour, so every
#: card has to offer *some* way to go look. A search is honestly labelled as a search.
SEARCH = "https://www.google.com/search?q={q}"

#: Fields copied straight off the ledger row. Listed rather than splatted so a new ledger field
#: cannot silently reach the page — notably anything private that lands in the schema later.
PASSTHROUGH = (
    "id",
    "company",
    "company_slug",
    "role",
    "url",
    "location",
    "score",
    "level_band",
    "stage",
    "outcome",
    "posting_live",
    "posted_date",
    "first_seen",
    "last_touched",
    "position_folder",
)

_CLI = "uv run python -m src.jobcore.pipeline"


def commands(opp_id: str) -> dict[str, str]:
    """Ready-to-paste CLI strings for the copy buttons.

    Strings for a human to run, never executed by the server. The dashboard is read-only
    precisely so that ``record_submission``'s second-route refusal and ``validate``'s schema
    check stay the only way state changes.
    """
    return {
        "applied": f"{_CLI} set {opp_id} --stage APPLIED",
        "in_process": f"{_CLI} set {opp_id} --stage IN_PROCESS",
        "rejected": f'{_CLI} set {opp_id} --stage CLOSED --outcome THEY_REJECTED --note ""',
        "declined": f'{_CLI} set {opp_id} --stage CLOSED --outcome I_DECLINED --note ""',
        "submit": f"{_CLI} submit {opp_id} --channel <channel> --name <name>",
        # What you saw when you opened the posting. Separate from the decision commands above:
        # a dead posting is a market fact, never a decision to stop pursuing.
        "live_open": f"{_CLI} liveness {opp_id} --status OPEN",
        "live_listed": f"{_CLI} liveness {opp_id} --status LISTED",
        "live_closed": f"{_CLI} liveness {opp_id} --status CLOSED",
    }


def checked_receipt(opp: dict, today: str) -> dict | None:
    """The stored hand-check, with its age, or ``None`` if nobody has looked."""
    checked = opp.get("posting_checked")
    if not checked:
        return None
    return {**checked, "age_days": pipeline._days_between(checked.get("date", ""), today)}


def open_target(opp: dict, today: str | None = None) -> tuple[str, str, str]:
    """Where the card's one button goes, and what it may honestly promise. ``(url, kind, label)``.

    The label is derived from state rather than fixed, because a fixed one lies. An "apply" button
    sat on Amdocs/AI Evangelist Lead while that req was ``POSTING_CLOSED`` — the board's own job is
    to stop exactly that kind of confident-but-dead row from consuming an hour.
    """
    url = (opp.get("url") or "").strip()
    if not url:
        query = f"{opp.get('company', '')} {opp.get('role', '')} job".strip()
        return SEARCH.format(q=quote_plus(query)), "search", "Search the web"

    if opp.get("posting_live") == "CLOSED" or opp.get("stage") == "CLOSED":
        return url, "closed", "View closed posting"
    if pipeline.needs_eyeball(opp, today):
        return url, "check", "Check status"
    return url, "open", "Open posting"


def card(ledger: dict, opp: dict, bucket_name: str, today: str) -> dict:
    """One opportunity as the page consumes it."""
    opp_id = opp.get("id", "")
    open_url, open_kind, open_label = open_target(opp, today)
    out = {k: opp.get(k) for k in PASSTHROUGH}
    out.update(
        {
            "bucket": bucket_name,
            "open_url": open_url,
            "open_kind": open_kind,
            "open_label": open_label,
            "route": pipeline.route_label(opp),
            "route_conflict": pipeline.route_conflict(opp),
            "needs_eyeball": pipeline.needs_eyeball(opp, today),
            "checked": checked_receipt(opp, today),
            "submitted": bool(opp.get("submissions")),
            "prior_outcomes": pipeline.prior_outcomes(ledger, opp.get("company_slug", ""), opp_id),
            "days_since_touch": pipeline._days_between(opp.get("last_touched") or "", today),
            "commands": commands(opp_id),
        }
    )
    return out


#: Extensions worth naming in the detail panel. Everything else on disk is ignored rather than
#: listed as "other" — the panel answers "what has been prepared here", not "what is in this folder".
_DOC_KINDS = {".md": "markdown", ".pdf": "pdf", ".drawio": "diagram", ".html": "html"}

#: The one directory a position folder may live under. Rooted at THIS repo's ``positions/``, not
#: matched by name: an earlier version asked whether any ancestor was *called* ``positions``, which
#: any decoy directory anywhere on the filesystem would satisfy. Module-level so tests can point it
#: at a temporary tree.
POSITIONS_ROOT = pipeline.REPO_ROOT / "positions"


def _documents(folder: str | None) -> list[dict]:
    """File **names** under a position folder. Never contents, never paths.

    Two guards, in this order:

    * **Confinement.** The resolved folder must sit strictly inside :data:`POSITIONS_ROOT` — this
      repo's actual ``positions/`` directory. A pointer like ``positions/../../..`` resolves out of
      it and yields nothing. This is belt-and-braces — ``position_folder`` comes off the ledger,
      never off the URL, so a request cannot choose the path — but the ledger is hand-editable and
      one bad pointer should not turn a read-only board into a file browser. Note it is *rooted*,
      not name-matched: asking whether any ancestor is called ``positions`` would accept a decoy
      directory of that name anywhere on the machine.
    * **Names only.** The entries carry ``name`` and ``kind`` and nothing else. No body, no path,
      no size. ``positions/`` holds salary floors, interviewer dossiers and rejection debriefs;
      the whole design premise is that the server never reads their contents.

    Filenames themselves can carry a person's name (``DEBRIEF_NITZAN_2026-08-03.md``). That is the
    same category the board already displays in its route labels ("head_hunter/Noa") on the same
    loopback-only page, so listing them is consistent rather than a new exposure — but it is the
    reason this stays loopback-only and the ledger stays out of any published artefact.
    """
    if not folder:
        return []
    try:
        resolved = Path(folder).resolve()
    except OSError:
        return []
    if not resolved.is_dir():
        return []
    try:
        root = Path(POSITIONS_ROOT).resolve()
    except OSError:
        return []
    if resolved == root or not resolved.is_relative_to(root):
        return []

    out: list[dict] = []
    try:
        entries = sorted(resolved.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []
    for entry in entries:
        if not entry.is_file() or entry.name == profile_mod.PROFILE_FILENAME:
            continue
        kind = _DOC_KINDS.get(entry.suffix.lower())
        if kind:
            out.append({"name": entry.name, "kind": kind})
    return out


def detail(ledger: dict, opp_id: str, today: str | None = None) -> dict | None:
    """Everything the per-position panel shows, or ``None`` when the id is unknown.

    Assembled from the two sources the repo keeps deliberately separate — state truth in the
    ledger, content truth in ``positions/`` — without ever merging them into the ledger schema.
    """
    today = today or pipeline.today_str()
    buckets = pipeline.bucket(ledger)
    for name, rows in buckets.items():
        for opp in rows:
            if opp.get("id") == opp_id:
                folder = opp.get("position_folder")
                prof: dict | None = None
                problems: list[str] = []
                if folder:
                    prof, problems = profile_mod.load_profile(folder)
                return {
                    "card": card(ledger, opp, name, today),
                    "profile": prof,
                    # A profile that failed validation is withheld and *reported*. Silence would
                    # read as "this company has no snapshot" when the truth is "it has an unsafe one".
                    "profile_error": "; ".join(problems) if problems else None,
                    "events": list(opp.get("events") or []),
                    "risks": list(opp.get("risks") or []),
                    "applied_date": pipeline.applied_date(opp),
                    "documents": _documents(folder),
                }
    return None


def alarms(ledger: dict, today: str) -> list[dict]:
    """Decisions that decay, most urgent first.

    Both kinds already existed in ``pipeline.py`` but only fired when somebody rendered the
    markdown board and read it. Axon 911 was pitched by name on 07-30 with the CV built; by 08-05
    another candidate was in final HR, and nothing had tracked the gap. On a page you glance at,
    these are continuous.
    """
    out: list[dict] = []

    for opp in ledger.get("opportunities", []):
        if opp.get("stage") == "CLOSED" or pipeline.is_suppressed(ledger, opp):
            continue
        if pipeline.route_conflict(opp):
            out.append(
                {
                    "kind": "route_conflict",
                    "id": opp.get("id", ""),
                    "company": opp.get("company", ""),
                    "role": opp.get("role", ""),
                    "text": "reachable by more than one route — resolve it before any CV goes out",
                }
            )

    for opp, age in pipeline.unsent_person_routes(ledger, today):
        named = pipeline.route_label(opp)
        out.append(
            {
                "kind": "route_opened_nothing_sent",
                "id": opp.get("id", ""),
                "company": opp.get("company", ""),
                "role": opp.get("role", ""),
                "age_days": age,
                "text": f"{named} opened {age} days ago, nothing submitted",
            }
        )

    return out


def view(ledger: dict, today: str | None = None, mtime: float = 0.0) -> dict:
    """The whole board as one JSON-serialisable dict."""
    today = today or pipeline.today_str()
    buckets = pipeline.bucket(ledger)
    problems = pipeline.validate(ledger)

    return {
        "generated": today,
        "ledger_mtime": mtime,
        "counts": {name: len(rows) for name, rows in buckets.items()},
        "validate": {"ok": not problems, "problems": problems},
        "alarms": alarms(ledger, today),
        "cards": [card(ledger, opp, name, today) for name, rows in buckets.items() for opp in rows],
    }
