"""Comeet board discovery — resolve a board's public token, harvest new boards.

Two gaps this closes.

**1. slug+uid is not enough to poll.** ``comeet.fetch`` needs ``{uid, token}`` and
returns ``[]`` without both. Every cheap source of Comeet companies — a job URL, a
``site:comeet.com/jobs`` search hit, an old ranked CSV — yields only ``slug`` and
``uid``. So "just paste the harvested slugs into ats_targets.json" silently produces
targets that poll nothing: no error, no jobs, a board you believe you are watching.
The public careers page embeds all three fields in one JSON blob, which makes
slug+uid → token a pure scrape::

    GET https://www.comeet.com/jobs/<slug>/<uid>
    … "company_uid": "68.006", "token": "8663…29FE", "slug": "fetcherr" …

Validated against the four boards already configured in ``ats_targets.json``
(Fetcherr, Cognyte, AllCloud, WSC Sports) — all four tokens matched byte-for-byte.
Two things that bite: token width varies (28–38 hex chars observed live), so nothing
here assumes one; and the uid **must** be in the path — ``/jobs/<slug>`` alone does
not serve the blob.

**2. Tier-1 Comeet polling is blind to unconfigured companies.** It only ever sees
boards already in ``ats_targets.json``. ``parse_board_urls`` turns a blob of search
results into candidate boards, ``new_boards`` diffs them against what is configured,
and a dedicated delta store stops a run re-proposing the same names.

Scope boundaries, both deliberate:

- This module **never writes** ``ats_targets.json``. Adding a board is a human call
  (a wrong token is invisible at poll time), so the output is a proposal.
- The web search itself stays in the skill layer. This module only parses what it is
  handed, which keeps it deterministic and offline-testable.
- Its delta store is its own file, never the targeted poll's, so a discovery run
  cannot corrupt tier-1's notion of which *jobs* it has already seen.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from ..console import force_utf8_output
from ..paths import JOB_TRACKING
from .base import http_get_text

CAREERS_URL = "https://www.comeet.com/jobs/{slug}/{uid}"
SEEN_STORE_DEFAULT = JOB_TRACKING / ".last_comeet_discovery_seen.json"

# A Comeet company uid looks like "68.006" / "41.00B" / "F7.008" — two hex chars,
# a dot, three hex chars. Tight enough that a job id or a greenhouse token can't
# be mistaken for one.
_UID = r"[0-9A-Fa-f]{2}\.[0-9A-Fa-f]{3}"

# Primary: the three fields as the live pages emit them, in order.
_TRIPLE_RE = re.compile(
    r'"company_uid":\s*"(?P<uid>' + _UID + r')"\s*,\s*'
    r'"token":\s*"(?P<token>[0-9A-Fa-f]{12,80})"\s*,\s*'
    r'"slug":\s*"(?P<slug>[A-Za-z0-9_-]{1,60})"'
)
# Fallback: uid and token independently, in case Comeet reorders its JSON. The slug
# is the one field we can afford to lose — the caller passed it in.
_TOKEN_ONLY_RE = re.compile(r'"token":\s*"(?P<token>[0-9A-Fa-f]{12,80})"')
_UID_ONLY_RE = re.compile(r'"company_uid":\s*"(?P<uid>' + _UID + r')"')

# A board URL, with or without the trailing /<job-slug>/<job-id>.
_BOARD_URL_RE = re.compile(
    r"comeet\.com/jobs/(?P<slug>[A-Za-z0-9_-]{1,60})/(?P<uid>" + _UID + r")",
    re.I,
)

# Titles Comeet/Spark Hire serves for boards whose metadata hasn't been filled in.
# They resolve to valid board URLs but carry no role information, so they are noise
# in a discovery sweep rather than a signal.
GENERIC_TITLE_MARKERS = (
    "spark hire recruit jobs",
    "spark hire jobs",
    "comeet jobs",
    "careers page",
    "job board",
)


def extract_board(html: str | None) -> dict | None:
    """Pure: a Comeet careers page → ``{slug, uid, token}``, or None if absent.

    Returns None rather than raising: a 404, an anti-bot page and a redesigned
    careers page are all "this board did not resolve", and the caller skips it.
    """
    if not html:
        return None
    m = _TRIPLE_RE.search(html)
    if m:
        return {"slug": m.group("slug"), "uid": m.group("uid"), "token": m.group("token")}
    tm, um = _TOKEN_ONLY_RE.search(html), _UID_ONLY_RE.search(html)
    if tm and um:
        return {"slug": "", "uid": um.group("uid"), "token": tm.group("token")}
    return None


def resolve_board(slug: str, uid: str, get_text=http_get_text) -> dict | None:
    """slug+uid → ``{slug, uid, token}`` by scraping the public careers page.

    None on a blocked/empty/unparseable fetch. The input ``slug`` wins over a blank
    one from the page, since the caller already knows it.
    """
    if not slug or not uid:
        return None
    board = extract_board(get_text(CAREERS_URL.format(slug=slug, uid=uid)))
    if board is None:
        return None
    return {"slug": board["slug"] or slug, "uid": board["uid"] or uid, "token": board["token"]}


def to_target(board: dict, company: str | None = None) -> dict:
    """Board → the exact ``ats_targets.json`` comeet entry shape.

    That file stores no slug, so the slug survives only in the company name unless
    the human edits it — hence the titleized default.
    """
    slug = board.get("slug", "")
    return {
        "company": company or slug.replace("-", " ").replace("_", " ").title(),
        "ats": "comeet",
        "uid": board.get("uid", ""),
        "token": board.get("token", ""),
    }


def parse_board_urls(text: str | None) -> list[dict]:
    """Pure: any blob of text → unique ``{slug, uid}`` boards, first-seen order.

    Deliberately tolerant of what it is fed — search-result output, an old ranked
    CSV, a pasted list — because the search layer's shape is not this module's
    concern. Slugs are lowercased so ``/UPWIND/`` and ``/upwind/`` don't both count.
    """
    if not text:
        return []
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for m in _BOARD_URL_RE.finditer(text):
        key = (m.group("slug").lower(), m.group("uid").upper())
        if key in seen:
            continue
        seen.add(key)
        out.append({"slug": key[0], "uid": m.group("uid")})
    return out


def is_generic_title(title: str | None) -> bool:
    """True for placeholder titles that carry no role information."""
    if not title or not title.strip():
        return True
    low = title.strip().lower()
    return any(marker in low for marker in GENERIC_TITLE_MARKERS)


def parse_board_records(records) -> list[dict]:
    """``[{title, url}, …]`` → boards, dropping placeholder-titled hits.

    The title-aware sibling of ``parse_board_urls``. Noise can only be filtered when
    the caller actually has titles — a bare URL sweep cannot tell a real posting from
    a Spark-Hire placeholder, so the search layer should hand over structured records
    whenever it has them.
    """
    if not isinstance(records, list):
        return []
    kept: list[str] = []
    for r in records:
        if not isinstance(r, dict):
            continue
        if is_generic_title(r.get("title")):
            continue
        kept.append(str(r.get("url") or ""))
    return parse_board_urls("\n".join(kept))


def _configured_uids(targets: list[dict]) -> set[str]:
    """uids of the comeet targets only — a greenhouse token is not a uid."""
    return {str(t.get("uid", "")).upper() for t in targets or [] if t.get("ats") == "comeet" and t.get("uid")}


def new_boards(
    found: list[dict],
    configured: list[dict],
    seen: set[tuple[str, str]] | None = None,
) -> list[dict]:
    """Boards that are neither configured nor already proposed in an earlier run."""
    known = _configured_uids(configured)
    seen = seen or set()
    out = []
    for b in found:
        uid = str(b.get("uid", "")).upper()
        slug = str(b.get("slug", "")).lower()
        if uid in known or (slug, uid) in seen:
            continue
        out.append(b)
    return out


def load_seen_boards(store: Path) -> set[tuple[str, str]]:
    """Read the discovery delta store → set of ``(slug, uid)`` already proposed."""
    try:
        rows = json.loads(Path(store).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(rows, list):
        return set()
    out = set()
    for r in rows:
        if isinstance(r, (list, tuple)) and len(r) == 2:
            out.add((str(r[0]).lower(), str(r[1]).upper()))
    return out


def save_seen_boards(store: Path, boards: list[dict]) -> None:
    """Merge this run's boards into the store. **Accumulates** — never overwrites.

    Same rule as ``ats_ingest.save_seen``: overwriting there made the delta compare
    against only the immediately-previous run, so a board seen two runs ago
    re-surfaced as new and the proposal list oscillated. Do not repeat that.
    """
    merged = sorted(
        load_seen_boards(store) | {(str(b.get("slug", "")).lower(), str(b.get("uid", "")).upper()) for b in boards}
    )
    try:
        Path(store).parent.mkdir(parents=True, exist_ok=True)
        Path(store).write_text(json.dumps([list(k) for k in merged], indent=2), encoding="utf-8")
    except OSError as e:
        print(f"comeet-discover: could not write {store}: {str(e)[:120]}", file=sys.stderr)


# ------------------------------------------------------------------------- CLI


def _load_targets(path: Path) -> list[dict]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")).get("targets", [])
    except (OSError, json.JSONDecodeError, AttributeError):
        return []


def main(argv: list[str] | None = None) -> int:
    force_utf8_output()
    ap = argparse.ArgumentParser(
        prog="python -m src.jobcore.ats.comeet_discover",
        description="Resolve Comeet board tokens and propose unconfigured boards. Never edits ats_targets.json.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("resolve", help="slug/uid pairs → pollable ats_targets.json entries")
    r.add_argument("pairs", nargs="*", help="slug/uid, e.g. monday/41.00B")
    r.add_argument("--from-file", type=Path, help="harvest slug/uid pairs from any text/CSV file")
    r.add_argument("--targets", type=Path, default=Path("ats_targets.json"))
    r.add_argument("--all", action="store_true", help="include boards already configured")

    d = sub.add_parser("discover", help="search-result blob → boards not yet configured")
    d.add_argument("--from-file", type=Path, required=True, help="file holding search results")
    d.add_argument("--targets", type=Path, default=Path("ats_targets.json"))
    d.add_argument("--seen-store", type=Path, default=SEEN_STORE_DEFAULT)
    d.add_argument("--record", action="store_true", help="mark proposals as seen")

    args = ap.parse_args(argv)
    configured = _load_targets(args.targets)

    boards: list[dict] = []
    if getattr(args, "from_file", None):
        try:
            blob = Path(args.from_file).read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            print(f"comeet-discover: cannot read {args.from_file}: {e}", file=sys.stderr)
            return 1
        # A JSON list of {title, url} lets us drop placeholder-titled noise; any other
        # content is scanned as plain text, where titles simply aren't available.
        try:
            records = json.loads(blob)
        except json.JSONDecodeError:
            records = None
        if isinstance(records, list):
            boards = parse_board_records(records)
            dropped = len(parse_board_urls(blob)) - len(boards)
            if dropped > 0:
                print(f"comeet-discover: dropped {dropped} placeholder-titled hit(s)", file=sys.stderr)
        else:
            boards = parse_board_urls(blob)

    if args.cmd == "resolve":
        for p in args.pairs:
            slug, _, uid = p.partition("/")
            if slug and uid:
                boards.append({"slug": slug.lower(), "uid": uid})
        if not boards:
            print("comeet-discover: no slug/uid pairs given", file=sys.stderr)
            return 1
        todo = boards if args.all else new_boards(boards, configured)
        skipped = len(boards) - len(todo)
        resolved: list[dict] = []
        failed: list[dict] = []
        for b in todo:
            got = resolve_board(b["slug"], b["uid"])
            (resolved if got else failed).append(got or b)
            print(f"  {'ok  ' if got else 'FAIL'} {b['slug']}/{b['uid']}", file=sys.stderr)
        print(json.dumps([to_target(b) for b in resolved], indent=2))
        print(
            f"comeet-discover: resolved={len(resolved)} failed={len(failed)} "
            f"already-configured={skipped} (review, then paste into ats_targets.json)",
            file=sys.stderr,
        )
        if failed:
            print(
                "  unresolved: " + ", ".join(f"{b['slug']}/{b['uid']}" for b in failed),
                file=sys.stderr,
            )
        return 0

    # discover
    seen = load_seen_boards(args.seen_store)
    proposals = new_boards(boards, configured, seen=seen)
    print(json.dumps(proposals, indent=2))
    print(
        f"comeet-discover: found={len(boards)} new={len(proposals)} "
        f"(configured+seen filtered out {len(boards) - len(proposals)})",
        file=sys.stderr,
    )
    if args.record and proposals:
        save_seen_boards(args.seen_store, proposals)
        print(f"comeet-discover: recorded {len(proposals)} in {args.seen_store}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
