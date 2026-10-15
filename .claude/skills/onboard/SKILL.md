---
name: onboard
description: "First-run interview for a new job seeker: asks about field, level, geography, company size, salary and exclusions, then writes profile.json, ats_targets.json and the local workspace folders (outside the repo). Use when there is no profile yet, or to revise an existing one. Trigger: 'use onboard', 'set me up', 'start'."
model: "sonnet"
allowed-tools: ["Read", "Write", "Edit", "Glob", "Bash", "AskUserQuestion"]
---

# Onboard — interview the user, build their private workspace

Goal: turn a short conversation into a validated `$PROFILE_DIR/profile.json`, a starter
`ats_targets.json`, and the local folder skeleton. **Everything personal is written OUTSIDE this
repo** (`PROFILE_DIR`). Never write personal data inside the clone, and never ask for or accept
passwords, API keys or tokens in chat — tell the user to paste secrets into `.env` / `.mcp.json`
themselves.

## Step 0 — Orient (silent)
1. `echo $PROFILE_DIR` and check `.env`. If unset, propose `~/job-search` and confirm once.
2. If `$PROFILE_DIR/profile.json` exists, read it and run in **revise mode**: show the
   `uv run python -m src.jobcore.profile --brief` summary and ask only what the user wants to change.
3. **Language:** hold the whole interview in the language the user writes in (e.g. Hebrew), and put it
   first in `locale.languages`. Field values may be in either language; add Hebrew and English
   variants of target titles/keywords. See `docs/HEBREW.md`.
4. Be kind: the user may have just lost their job. Keep the tone practical and unhurried.

## Step 1 — The interview
Ask in small batches (2–4 questions at a time, prefer multiple-choice via AskUserQuestion; the user
can always type their own answer). Skip anything they already told you.

**A. Field and level** → `roles.*`, `skills.*`
- Field: software engineering / programming, team lead, engineering management, product, data/ML, other.
- Seniority now and the level they want next (same level, step up, or open to either).
- IC only, or lead/manager roles? (`roles.require_leadership` — leave `false` for ICs, otherwise every IC role scores 1.)
- Exact titles they want; titles they never want (`target_titles`, `exclude_titles`).
- Their actual stack/skills, and the 3–5 that matter most (`keywords`, `boost_keywords`).

**B. Geography** → `geo.*`, `locale.timezone`
- City/region, remote / hybrid / on-site, willingness to commute or relocate.
- `geo.mode`: start `preferred` (rank by location). Use `required` only if anything outside the list is truly unacceptable.

**C. Company** → ats targets, `research`
- Size/stage: startup, scale-up, enterprise, public sector, no preference.
- Industries to prefer or never touch (`domain_exclusions`).
- Named target companies, if any (you will look up their job boards in Step 3).

**D. Compensation** → `salary.*`, `locale.currency`
- Currency, period (year/month), floor (walk-away), target, and an optimistic "open" number.

**E. Practicalities** → `locale.languages`, `identity`
- Languages they work in; languages they do NOT have (`facts.knockout_languages`).
- Urgency/pace (applying this week vs. exploring) → `research.depth` (`quick` default, `deep` for finalists).
- Anything private that must never appear in a CV or message (an ongoing legal/HR process, dates) →
  `privacy.never_mention` / `privacy.private_dates`. Ask gently; it is optional.
- Their CV: ask them to place it at `$PROFILE_DIR/cv.md` (Markdown, with a `# Full Name` heading).
  If they paste it in chat, write it there for them.

## Step 2 — Write the profile
1. Start from `profiles/_template.json`; copy to `$PROFILE_DIR/profile.json`; replace the example
   values with the user's answers; drop the `_comment`/`_field` helper keys.
2. Leave `facts` empty unless the user gave ground truth — empty makes the honesty gates print
   `SKIPPED[...]`, which is correct and deliberate. Say so. Do not invent facts.
3. Run `uv run python -m src.jobcore.profile validate`. Fix every error; re-run until clean.
4. Run `uv run python -m src.jobcore.profile --brief` and read the summary back for confirmation.

## Step 3 — Job boards
Copy `ats_targets.example.json` to `$PROFILE_DIR/ats_targets.json` (or the path the repo reads —
check `.env.example`). For each named target company, ask the `liveness-prober` agent for its
Greenhouse/Comeet token; add only verified boards. No targets yet is fine — say that
`job-radar` will still use LinkedIn-guest and (if configured) Exa discovery.

## Step 4 — Build the workspace
Follow `AGENTS.md` § "Workspace layout": create `positions/`, `job-tracking/`, `reasoning/`,
`docs/`, `archive/`, `tasks/`, `companies/` under `$PROFILE_DIR` with a one-line README in each.
Then write a `NEXT_STEPS.md` in `tasks/` with the first three concrete actions.

## Step 5 — Hand off
Tell the user, in their language: what was created and where; that nothing personal is inside the
repo; and the first command, `use job-radar`. Do not start scanning unasked.

## Rules
- Ask, don't assume; never guess salary, location or seniority.
- Never commit anything under `$PROFILE_DIR`; never print secrets.
- If `validate` fails, you are not done.
