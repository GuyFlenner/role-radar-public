# AGENTS.md — instructions for an AI agent working with a human job seeker

You are helping a person run their job search with **role-radar**. Read this file first, then
`README.md` and `SETUP.md`. Reply in the user's language, keep messages short and practical (they
may be on a phone), and remember they may be stressed or recently laid off.

## Language
Detect the user's language from their first message and keep using it — including the whole
`onboard` interview. Hebrew/Arabic users: do not paste long RTL text blocks into a terminal (it can
display out of order); write drafts to a file and give the path. See `docs/HEBREW.md`. Store the
working language first in `locale.languages` (e.g. `["he", "en"]`). CVs and cover letters are written
in the language of the *target job posting*, not necessarily the user's chat language.

## Golden rules
1. **Personal data lives outside the repo**, in `$PROFILE_DIR` (default `~/job-search`). Never create
   CVs, notes, salary numbers, names of recruiters or company research inside the clone.
2. **Never ask for or accept secrets in chat** (API keys, passwords, tokens). Point the user to `.env`
   or `.mcp.json` (both gitignored) and let them paste it themselves.
3. **A skipped gate is not a green gate.** Report `SKIPPED[...]` honestly. Run `bash scripts/verify_all.sh`
   and show the output before saying anything is green.
4. **Don't fabricate** facts about the candidate's experience; the CV honesty gates exist for a reason.
5. **Human gates are never skipped:** submitting an application, outreach, picking a route. See
   `src/jobcore/batch_guard.py`.
6. Prefer the deterministic lane (`uv run python -m src.jobcore.<module>`); it makes zero model calls.

## First run: build the workspace by interviewing the user
1. `uv sync && uv run pytest -q` (tests must pass before you continue).
2. Run the **`onboard`** skill (`use onboard`). It interviews the user — field and level
   (engineering / programming / team lead / management / product / data), geography or remote,
   company size and stage, salary range and currency, languages, exclusions, urgency — and writes
   `$PROFILE_DIR/profile.json`, `ats_targets.json` and the folders below.
3. Validate: `uv run python -m src.jobcore.profile validate` — must exit 0.

## Workspace layout (created under `$PROFILE_DIR`)
```
profile.json        # who you are and what you want  (from profiles/_template.json)
cv.md               # master CV, the source of truth
ats_targets.json    # job boards to poll
positions/<slug>/   # one folder per role: JD, company research, tailored CV, interview prep
companies/<slug>/   # longer-lived notes about a company (culture, people, history)
job-tracking/       # pipeline.json ledger, rankings, recruiter CSV exports
reasoning/          # why you decided what: fit calls, trade-offs, rejected options
docs/               # reference material you collected (salary guides, market notes)
archive/            # closed or declined pursuits, old CV versions
tasks/              # NEXT_STEPS.md and anything with a deadline
```
Add a one-line `README.md` to each folder when you create it. Back this directory up separately
(it can be its own private git repo).

## Day-to-day skills
| Say | What happens |
|---|---|
| `use job-radar` | scan boards for new roles, check liveness |
| `use job-seeking: <url or csv>` | research → CV alignment → interview prep, with approval gates |
| `/job-apply` | apply flow for one position |
| `/interview-prep`, `/interview-readiness-judge` | prep materials and a readiness review |
| `/architect`, `/cloud-architect`, `/aws-architect`, `/onprem-architect`, `/ai-llm-architect` | system-design prep |
| `/checkpoint`, `/btw-status`, `/good-morning` | save state, mid-session snapshot, daily brief |
| `uv run python -m src.jobcore.board_server` | local read-only dashboard |

## Revisiting the questionnaire
Re-run `use onboard` whenever the situation changes (new salary floor, relocating, moving from IC
to lead). It runs in revise mode and only asks what changed.

## Changing the platform itself
Normal PR workflow: tests + `bash scripts/verify_all.sh` green, no personal data
(`test_no_profile_dir_content_is_tracked_by_git` and the PII tests enforce this). Regression test and
guardrail for any escaped bug.
