---
name: good-morning
description: "Daily read-only status brief for this job-seeking workspace — surfaces today/this-week schedule items, in-progress positions, who owes you a reply, and local git state. Never starts new work. Invoke manually with /good-morning."
model: "sonnet"
allowed-tools: ["Read", "Glob", "Bash"]
---

# Good Morning Brief

Produce a focused daily brief. Do NOT start new work — this is a read-and-report skill only.

**Trigger phrases**: "good morning", "morning brief", "what's on today", "what do I have today"

> Adapted for this job-seeking workspace from the upstream `claude-sdlc` `good-morning` skill.
> No Slack/Jira/PR sources here (this repo has none); no skill-library-health section (this repo
> has no `skill_lint.py`/`skill_curator.py`) — dropped, not stubbed. Pure read: zero network calls.

---

## Step 1 — Load context sources (read in parallel)

1. `job-tracking/INTERVIEW_INDEX.md` — the schedule table and "tonight/this week" section. This
   is this repo's de facto status board; read it in full.
2. The pipeline ledger — `uv run python -m src.jobcore.pipeline render` — for which positions
   are live and at what stage. This replaced a hand-kept table in `CLAUDE.md` that went stale.
3. `~/.claude/projects/<repo-slug>/memory/MEMORY.md` — the index
   only. Follow the link to the **1–2 most recent** `_checkpoint_*.md` entries for "what was
   last worked on" — do not re-read every memory file.
4. `positions/*/STATUS.md` (glob) — read any that exist for extra per-position detail. Not every
   position has one (e.g. only `positions/centerity/STATUS.md` exists today) — skip silently
   where absent; absence is not an error.
5. Local git state:
   ```bash
   git log --oneline -5 2>/dev/null || true
   git status --short 2>/dev/null || true
   ```

## Step 2 — Staleness advisory (no action taken)

Check the mtime of `job-tracking/tracked_status.md` (job-radar's liveness output) if it exists.
If it is older than ~7 days, or the file does not exist, note one advisory line under Suggested
first action: `"job-radar hasn't run in N days — consider \`use job-radar\`."` This is advisory
text only — this skill never runs job-radar itself.

## Step 3 — Render the brief

Keep each section tight — bullets only, no prose. Target **under ~30 lines** total.

```
## Good Morning — {date, the candidate's local timezone, e.g. "2026-07-30 (Thursday, IDT)"}

### 🔴 Today / this week
[interviews or hard deadlines from INTERVIEW_INDEX.md within the next 48h-7d, or "None"]

### 🟡 In progress
[positions at an active pipeline phase per the ledger / STATUS.md — research,
CV-align, interview-prep, applied-awaiting-response]

### 👂 Waiting on others
[company/recruiter: what they owe you — last-contact date, drawn from memory/positions;
"None" if nothing is pending]

### 📂 Local state
[branch, uncommitted file count if any, age of the last checkpoint]

### 💡 Suggested first action
[single most impactful next step — imperative, 1 line. Append the staleness advisory from
Step 2 here if triggered.]

### ✅ Recently resolved
[only include this section if something closed/resolved since the last brief — omit entirely
otherwise; clean is the silent default, same rule upstream uses for its skill-library section]
```

---

## Behavior rules

- Read-and-report only. Never edit a file, never trigger another skill, never start new work.
- Timezone: the profile's `locale.timezone` (`uv run python -m src.jobcore.profile --brief`);
  never present a bare UTC time as local.
- **Redaction:** this brief only prints to the conversation — it never persists a new file —
  so it inherits the ambient redaction already applied by the sources it reads. It must still
  never echo the contents of any directory the user has marked private (legal, HR, termination
  or similar) even if another source references it — summarize as "private matter, see the
  private notes" and name the path, never the content.
- If an item from the last brief has since passed or resolved, move it to
  **✅ Recently resolved** instead of silently dropping it.
- If a source file is missing or empty, note it plainly (e.g. "INTERVIEW_INDEX.md not found")
  and continue with whatever else was read — never fail the whole brief over one missing source.
