# role-radar

A profile-driven job-search platform: poll job boards, score roles against **your** stack and
seniority, tailor a CV through deterministic quality gates, and track every opportunity in one
ledger.

**Built to be driven by an AI agent.** Clone it, open it in Claude Code (or any agent that reads
`AGENTS.md`), and say `use onboard`: the agent interviews you — field and level, geography, company
size, salary range, exclusions — and builds your private workspace. The platform is shared; your
job search is not: CV, pipeline and salary bands live outside this repo (`$PROFILE_DIR`).

→ **[AGENTS.md](AGENTS.md)** — instructions for the agent. → **[SETUP.md](SETUP.md)** — manual first run, ~15 minutes.

## Start here — with an AI agent (recommended)

You need: [Claude Code](https://claude.com/claude-code) (or another coding agent that reads `AGENTS.md`),
[`uv`](https://docs.astral.sh/uv/), Python 3.13, and git. Chrome/Edge is optional (needed only for Hebrew/Arabic PDFs).

```bash
git clone https://github.com/GuyFlenner/role-radar-public.git && cd role-radar-public
claude            # or open the folder in your agent of choice
```

Then paste this to the agent:

> Read AGENTS.md and README.md, set up this repo, and run the `onboard` interview with me.
> Talk to me in my own language. Keep all my personal data outside the repo.

The agent installs dependencies, runs the tests, interviews you (field and level, geography or remote,
company size, salary range, exclusions, urgency), writes your profile and workspace folders, and
tells you the first command to run. **Never paste passwords or API keys into the chat** — the agent
will point you to the file where you add them yourself.

**Hebrew / עברית:** supported — the agent answers in your language, Hebrew keywords match with
attached prefixes, the dashboard flips per line, and Hebrew CV PDFs render right-to-left.
See [docs/HEBREW.md](docs/HEBREW.md).

---

## What it does

| | |
|---|---|
| **Discover** | Greenhouse / Comeet / LinkedIn-guest polling, plus an opt-in Exa tier for careers-only postings |
| **Score** | Keyword match against your profile, seniority banding, geography, domain exclusions — all deterministic, zero LLM calls |
| **Verify** | Liveness on the apply URL, so a dead posting never reaches your shortlist |
| **Tailor** | CV alignment with gates for ATS coverage, phrase drift, cover-letter drift, page count, and factual honesty |
| **Track** | A committed ledger with stage, outcome, provenance, and a guard against sending one CV through two competing channels |

Everything career-shaped is configuration, not code. A backend lead in Tel Aviv and a frontend IC
in Berlin get sensibly different results from the same engine — there is a test that proves it.

## Quick start (manual, without an agent)

```bash
uv sync && uv run pytest -q

mkdir -p ~/job-search                              # outside this repo
cp profiles/_template.json ~/job-search/profile.json
$EDITOR ~/job-search/profile.json                  # every field is documented inline
echo 'PROFILE_DIR=~/job-search' >> .env

uv run python -m src.jobcore.profile validate
uv run python -m src.jobcore.ats_shortlist --config ats_targets.json
```

## Layout

```
src/jobcore/            deterministic core — no LLM calls
  profile.py              the candidate; everything else reads it
  scoring.py  gates.py    relevance, ranking, CV quality
  ats/  liveness.py       job-board providers and apply-URL checks
  pipeline.py             the opportunity ledger + board
  rag/                    retrieval over your own notes
.claude/skills/         the interactive layer (Claude Code)
profiles/
  _template.json          copy this to $PROFILE_DIR/profile.json
  _fixtures/              two SYNTHETIC profiles the CI matrix runs against
scripts/
  verify_all.sh           the CI contract
  check_parity.py         compare against an upstream repo after a sync
  setup-github.sh         publish your own private fork (refuses if personal data is tracked)
```

## Two rules worth knowing before you touch anything

**A skipped gate is not a green gate.** Gates that cannot run say `SKIPPED[...]` instead of
returning nothing. "No violations found" when nothing was checked reads as *your CV is honest* —
a much more expensive lie than a warning. Same for `cov=n/a` from the ATS gate.

**Your data never enters this repo.** `.gitignore` blocks it and a test fails the build if it
slips through. Keep your fork private if you add anything personal to it.

## Pipeline board — the local dashboard

```bash
uv run python -m src.jobcore.board_server        # -> http://127.0.0.1:8765
```

A localhost, read-only page over your `pipeline.json` ledger: filters by company/role/score/liveness,
with route-conflict alarms pinned on top. Zero model calls. State changes are CLI commands you run
yourself (`python -m src.jobcore.pipeline ...`). Never expose it or publish the ledger.
Full documentation: [`docs/PIPELINE_BOARD.md`](docs/PIPELINE_BOARD.md).

See [CLAUDE.md](CLAUDE.md) for the full operating contract.
