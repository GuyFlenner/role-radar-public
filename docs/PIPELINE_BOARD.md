# Pipeline Board — the local dashboard

A single localhost page over `job-tracking/pipeline.json`. It answers *"where does my next hour
go?"* without spending a Claude session, and keeps the decaying-decision alarms in front of you
continuously instead of only when someone renders the markdown board and reads it.

**Read-only by design.** It never writes the ledger. See [Why read-only](#why-read-only).

---

## Run it

```bash
uv run python -m src.jobcore.board_server
```

Then open <http://127.0.0.1:8765>.

| Flag | Default | |
|---|---|---|
| `--port N` | `8765` | Binds `127.0.0.1` only, never `0.0.0.0` |
| `--ledger PATH` | `job-tracking/pipeline.json` | Point it at a copy to experiment safely |
| `--no-open` | off | Don't launch a browser |

Stop it with Ctrl-C. Zero new dependencies (stdlib `http.server`) and **zero model calls** — this
is on the free deterministic lane with `shortlist`, `pdf`, `liveness` and `track_sweep`.

> The server lives as long as the terminal you started it in. Started from inside Claude Code, it
> dies with the session — run it in your own terminal to keep it up.

---

## What's on the page

**Count strip** — the six board buckets, straight from `pipeline.bucket()`. Click one to filter.

| Bucket | Means |
|---|---|
| `act_now` | in-band, not started, not blocked — the queue |
| `in_flight` | `APPLIED` / `IN_PROCESS` / `OFFER` |
| `watch` | posting died, or this company already rejected/declined you on another req |
| `above_band` | director/VP titles — a rung above target, kept not dropped |
| `closed` | pursuit closed, whatever the outcome |
| `suppressed` | company carries a `do_not_pursue` policy |

**Alarm banner** (above the fold, hidden when empty) — the reason the board exists:

- 🔴 **`route_conflict`** — this req is reachable by more than one route and none is marked
  `chosen`. Sending now risks an agency fee dispute or a duplicate-application flag.
- 🟠 **`route_opened_nothing_sent`** — a *person* introduced this and no submission is recorded,
  past `UNSENT_GRACE_DAYS` (2). A scan finding a posting costs nothing to sit on; a named
  introduction decays.

**Filters** — free text over company + role, minimum score, `posting_live` chips, and a
`bucket / company / score` grouping toggle. This is what makes the whole ledger reachable: the
markdown board prints only the top `ACT_NOW_LIMIT` rows and points the rest at raw JSON (on
2026-08-06 that was 12 shown out of 72).

**Keyboard** — `/` focuses the filter, `Esc` clears it.

**Live refresh** — polls `/api/board` every 5s and re-renders only when the ledger's mtime
changes, so a `pipeline set` run in another window appears by itself and an idle page does no work.

### A card

The left tab carries the score, coloured by `posting_live`. Scanning that edge alone tells you
what is real. Each card has exactly **one** action, whose wording and colour come from state —
never a fixed label:

| Button | When |
|---|---|
| `Open posting ↗` | posting confirmed `OPEN` or `LISTED` |
| `Check status ↗` | `needs_eyeball()` — nothing has established this, go look |
| `View closed posting ↗` | posting `CLOSED` or pursuit `CLOSED` — **never says "apply"** |
| `Search the web ↗` | no URL on file; a labelled web search, not a dead link |

The last two exist because of real defects: an "apply" button sat on Amdocs/AI Evangelist Lead
while that req was `POSTING_CLOSED`, and 28 of 110 rows carry no URL at all, so a fixed link
pointed at nothing.

`▸ manage` opens a drawer with whether anyone has checked the posting by hand, then two separate
rows of copy-to-clipboard commands — liveness above, decisions below. They are kept apart because
**a dead posting is a market fact, never a decision to stop pursuing.**

---

## Recording what you saw

The board sends you to the posting; this is how you record the answer. Without it the same rows
ask to be checked forever.

```bash
uv run python -m src.jobcore.pipeline liveness <id> --status CLOSED --note "404, req pulled"
```

`--status` is one of `OPEN` `LISTED` `CLOSED` `UNVERIFIED`.

It writes `posting_live` and a `posting_checked` receipt (`status`, `date`, `by: human`, `note`)
and **nothing else**. Three deliberate omissions, each because the alternative breaks something:

- **No history entry.** `bucket()` sends a row to WATCH on *any* sibling history, so logging a
  link check there would drag every other req at that company into WATCH.
- **No stage or outcome change.** That separation is why an `IN_PROCESS` role with a `CLOSED`
  posting reads as a normal late-stage pursuit rather than an alarm.
- **No effect on prunability.** A link check is not a decision, so it must not make an otherwise
  untouched row immune to `prune`.

### How a hand-check ranks against the automated sweep

`track_sweep` probes apply URLs and writes `posting_live` too. Precedence, in `_merge_liveness()`:

1. A **fresh positive probe** (`OPEN`/`CLOSED`) wins — postings genuinely get reposted and pulled.
2. Otherwise a **hand-check** wins — a person who opened the page and read it is evidence.
3. Otherwise `CLOSED` stays sticky, else `UNVERIFIED`.

A hand-check also clears `needs_eyeball()` for `CHECK_FRESH_DAYS` (14). That matters on
`JS_RENDERED_HOSTS` — `jobs.amdocs.com`, `phenompeople.com`, `myworkdayjobs.com` — where no
automated check can *ever* read the page, so before this the "check this" prompt was unclearable
by any means. It expires so a month-old glance is not sold as confirmation.

### The other ledger commands

The drawer's decision buttons copy these:

```bash
uv run python -m src.jobcore.pipeline set <id> --stage APPLIED
uv run python -m src.jobcore.pipeline set <id> --stage IN_PROCESS
uv run python -m src.jobcore.pipeline set <id> --stage CLOSED --outcome THEY_REJECTED --note "..."
uv run python -m src.jobcore.pipeline set <id> --stage CLOSED --outcome I_DECLINED --note "..."
uv run python -m src.jobcore.pipeline submit <id> --channel head_hunter --name "Sam"
```

Full ledger CLI (render, channel, policy, validate, dedupe, prune) is documented in `CLAUDE.md`.

---

## Why read-only

The guards that stop a CV going out twice live in `pipeline.py`: `record_submission()` refuses a
second route, and `validate()` enforces the schema. A UI writing `pipeline.json` directly would
bypass both — a green control that means nothing. So the server has **no write path at all**, and
mutations are copyable commands you run yourself.

That is enforced, not intended. Tests fail if anyone:

- imports anything but `load` / `LEDGER_DEFAULT` from `pipeline` into `board_server` (checked by
  AST, so adding `save` breaks the build);
- adds a handler for `POST` / `PUT` / `PATCH` / `DELETE` (all return `405`);
- binds anything but `127.0.0.1`;
- makes the ledger reachable by path traversal (routing is exact-match — no filesystem path is
  ever derived from a URL);
- reintroduces prose-that-looks-like-a-link, or a hardcoded action label;
- adds a view-model command that no button is wired to.

The server also sets `allow_reuse_address = False`. `HTTPServer` enables it by default, and on
Windows that lets a second process bind a port a live server is still using — both report success
while the *old* one keeps answering, so you restart the board and silently read stale data.

---

## Privacy

`pipeline.json` carries head-hunter names, rejection notes and salary context.

- Loopback bind only — nothing is exposed on the network, and no auth is needed because no one
  else can reach it.
- The page is fully self-contained: no CDN, no webfonts, no external requests. Outbound links
  carry `rel="noopener noreferrer"`.
- **GitHub Pages is not an option**, now or later: it is static so it could not serve the API, and
  publishing it would publish the data. The *code* is shareable; the *data* never leaves the
  machine.

---

## How it fits together

```
job-tracking/pipeline.json      truth. git-tracked. the UI NEVER writes it.
        │ pipeline.load()
        ▼
src/jobcore/pipeline.py         bucket() · validate() · unsent_person_routes()
        │                       route_label() · route_conflict() · needs_eyeball()
        ▼
src/jobcore/board_view.py       PURE: ledger dict → view-model dict. No I/O, no HTTP.
        ▼
src/jobcore/board_server.py     stdlib ThreadingHTTPServer, 127.0.0.1 only
        │   GET /                   → static/board.html
        │   GET /api/board          → view model JSON
        │   GET /api/position/<id>  → one position, lazily (see below)
        │   anything else    → 404; any non-GET verb → 405
        ▼
src/jobcore/static/board.html   one self-contained file: HTML + CSS + vanilla JS
```

`positions/<slug>/profile.json` is the second input, and it joins the payload only through
`src/jobcore/profile.py` → `validate_profile()`. See "The per-position panel" below.

**The browser classifies nothing.** `bucket()` runs once server-side and every card arrives
pre-tagged. If the page re-derived "is this ACT NOW", it could disagree with `PIPELINE_BOARD.md` —
the drift `src/jobcore/` exists to prevent. The route string is `pipeline.route_label()`, shared
with the markdown board so the two cannot diverge.

### `GET /api/board`

```jsonc
{
  "generated": "2026-08-06",
  "ledger_mtime": 1754400000.0,
  "counts": { "act_now": 72, "in_flight": 4, "watch": 5,
              "above_band": 7, "closed": 20, "suppressed": 2 },
  "validate": { "ok": true, "problems": [] },
  "alarms": [ { "kind": "...", "id": "...", "company": "...", "role": "...", "text": "..." } ],
  "cards":  [ { "id": "...", "bucket": "act_now", "score": 9,
                "route": "scan", "route_conflict": false, "needs_eyeball": false,
                "checked": null, "open_url": "...", "open_kind": "open",
                "open_label": "Open posting", "commands": { } } ]
}
```

### `GET /api/position/<id>` — the per-position panel

Fetched **lazily**, only when you expand a card's drawer, and cached client-side so the 5s poll
does not re-request it. Loading this for all 115 rows on every tick would multiply the payload for
data almost none of which is on screen.

```jsonc
{
  "card":          { /* the same card shape as /api/board */ },
  "profile":       { "industry": "…", "founded": 2005, "size": "…", "funding": "…",
                     "what_they_do": "…", "differentiator": "…", "tech_stack": ["Java"],
                     "sources": ["https://…"], "updated": "2026-08-07" },
  "profile_error": null,
  "events":        [ { "date": "2026-08-07", "event": "HR_SCREEN", "note": "…" } ],
  "risks":         [ { "date": "2026-08-07", "text": "…", "source": "glassdoor" } ],
  "applied_date":  "2026-08-07",
  "documents":     [ { "name": "COMPANY_RESEARCH.md", "kind": "markdown" } ]
}
```

**What it deliberately does not serve.** `documents` is **names and kinds only** — never a body,
never a path. `positions/` holds salary walk-away floors, named-interviewer dossiers with handling
advice, honest self-assessed gaps and clearance status; the server has no file-reading path for any
of it, and a test plants a sentinel string in a position file and asserts it never appears in a
response.

**The profile is a closed schema, and it is re-validated on every request.** `validate_profile()`
refuses (1) any undeclared key, (2) a sensitive key name, and (3) any *value* matching
compensation — `₪`, `NIS`, `ILS`, a `45-50K` band, "walk away", "floor ~". Rule (3) is the one that
matters: a key denylist alone would miss a salary floor pasted into `what_they_do`. A profile that
fails validation is **withheld entirely** — `profile` is `null` and `profile_error` says why, so a
rejected file reads as "unsafe snapshot" rather than "no snapshot".

**The id is never a path.** The URL suffix is an opportunity id, looked up in the ledger; the
folder comes off the matched row. It is additionally confined to a directory named `positions`, so
a hand-edited pointer like `positions/../../..` yields nothing rather than turning a read-only
board into a file browser.

### Recording what feeds it

```bash
uv run python -m src.jobcore.pipeline event <id> --type HR_SCREEN --note "Liat, 30m"
uv run python -m src.jobcore.pipeline risk  <id> --text "legacy stack per Glassdoor" --source glassdoor
```

Event types: `APPLIED` `HR_SCREEN` `TECH_SCREEN` `HOME_ASSIGNMENT` `SYSTEM_DESIGN` `MANAGER_ROUND`
`TEAM_ROUND` `FINAL_ROUND` `OFFER_RECEIVED` `FOLLOW_UP_SENT`. `APPLIED` is also written once,
automatically, the first time a row reaches `APPLIED`/`IN_PROCESS`/`OFFER` — that is the applied
date the board never had.

**The markdown board carries these too.** `pipeline render` shows `applied {date}`, `last: {ROUND}
{date}` and a `⚠ N watch-for` count on every IN FLIGHT line, and lists the recorded watch-fors for
the ACT NOW queue underneath its table. The full timeline and the profile stay browser-only — the
markdown board is a decision queue, not a record — but the question `events` was added to answer
("when did I apply?") is answerable in the artifact most people actually read.

### Which positions have a profile yet

Nothing backfills `profile.json`: a folder gets one the next time `/company-researcher` runs. An
invisible rollout is one that silently never happens, so:

```bash
uv run python -m src.jobcore.profile status [--missing]
```

Exits `0` when nothing is refused, `1` when an existing `profile.json` fails validation. **Missing
is not an error** — most positions simply predate the schema. `refused` is the row worth reading:
the file exists, so somebody wrote it, and the board is withholding it rather than showing it.

**`events` is a separate list from `history`, on purpose.** `history` carries `OUTCOMES` only and is
read *across siblings*: `bucket()` sends a row to WATCH whenever any other req at the same company
has history. Recording "HR screen on the 13th" there would drag every sibling into WATCH and freeze
them against `prune` — the same trap `set_liveness` refuses for link checks. An interview round is
something that happened to *this* req; only an outcome says how a pursuit ended. `risks` is
row-local for the same reason.

---

## Tests

```bash
uv run python -m pytest src/jobcore/tests/ -k board -q
bash scripts/verify_all.sh          # the full CI contract
```

| File | Covers |
|---|---|
| `tests/test_board_view.py` | bucketing agrees with `pipeline.bucket()`, alarms, button labels, hand-check receipts |
| `tests/test_board_server.py` | real sockets on ephemeral ports, the read-only guard, traversal, double-bind |
| `tests/test_board_page.py` | contract between the page and the view model — the JS has no type checker, so these are it |

`test_board_page.py` matters more than it looks: a renamed view-model field would fail silently in
the browser as `undefined`, rendering a strip that is merely *wrong*. Those tests are the type
check, and they were mutation-verified — renaming a field and adding an unwired command each make
them fail.

---

## Troubleshooting

**`OSError: [Errno 10048]` / "address already in use"** — a board is already running on that port.
That error is the feature; find it and stop it, or use `--port`.

```powershell
Get-NetTCPConnection -LocalPort 8765 -State Listen | Select-Object OwningProcess
```

**The page shows "ledger unreachable"** — the server stopped. Restart it; the page recovers on its
next poll without a refresh.

**A card looks stale** — the page re-renders only when the ledger's mtime changes. If you edited
`pipeline.json` by hand in a way that preserved mtime, hit refresh.

**The board disagrees with `PIPELINE_BOARD.md`** — it shouldn't; both call `pipeline.bucket()`.
Re-render the markdown (`pipeline render`), which is generated and may simply be older.

---

**See also:** `docs/superpowers/specs/2026-08-06-pipeline-board-ui-design.md` for the design
rationale, and `CLAUDE.md` for the full ledger vocabulary.
