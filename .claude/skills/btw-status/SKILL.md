---
name: btw-status
description: "Reports the current status of THIS session's work: recent commits, which position folders were touched, any open HITL gate, gate-runner verdicts run this session, and the single next action. A mid-session snapshot, distinct from /good-morning's daily/calendar-oriented brief. Manual trigger only."
model: sonnet
allowed-tools: ["Read", "Glob", "Grep", "Bash"]
---

# btw-status

Answer: **"What is the current status of this session's work? Is it done?"**

This is a context snapshot, not a task — do not start new work, do not clear any gate, do not
approve anything on the candidate's behalf.

> Adapted for this job-seeking workspace from the upstream `claude-sdlc` `btw-status` skill.
> The upstream version reports SDLC phase (0–9)/PR/deployment state — this repo retired that
> pipeline 2026-07 in favor of native job-seeking skills, so this version reports what this
> repo actually tracks instead. See the `good-morning` skill for the complementary daily brief;
> this skill is about *this session*, not the calendar.

---

## What to check (in order)

1. **Recent commits this session** — `git log --oneline -10` and `git status --short`. Call out
   anything still uncommitted, and anything uncommitted that predates this session (don't claim
   it as this session's work — check timestamps/content against what was actually discussed).
2. **Position folders touched this session** — any new/modified `positions/{slug}/` directories.
   For each: was it approved (Gate 2 / `lead_approval` / `apply`), or is it still awaiting a
   human decision?
3. **Open HITL gates** — per `src/jobcore/batch_guard.py`'s `NEVER_SKIPPABLE`
   (`lead_approval`, `outreach`, `submit`, `apply`, `route`): is anything from this session
   sitting at one of these, waiting on the candidate's own yes/no? Name it explicitly — never imply a gate
   was cleared if it wasn't.
4. **Gate-runner / CI verdicts run this session** — if `scripts/verify_all.sh` or
   `src.jobcore.check` was actually run (not just assumed) this session, report the last known
   verdict and what it covered. If nothing was verified this session, say so — do not infer
   "probably fine" from an earlier unrelated run.
5. **Pipeline ledger changes** — any new/modified rows in `job-tracking/pipeline.json` this
   session (`pipeline validate` should still pass); note if `PIPELINE_BOARD.md` needs a
   `pipeline render` to catch up.
6. **Anything pushed vs. only committed locally** — `git log origin/main..HEAD` /
   `git log HEAD..origin/main` to catch a diverged or un-pushed state.
7. **Background agents still in flight** — any subagent/task launched this session that hasn't
   reported back yet (check for pending task notifications, don't guess at a result you haven't
   received).

---

## Output format

Keep it short. Use this structure:

```
## Session Status — {date}

**Branch**: [branch name] — [last commit subject], [pushed / N ahead of origin]

### Done this session
- [bullet list of completed, verified steps — each one either committed+pushed, or explicitly
  marked "committed, not pushed" / "not yet committed"]

### Open HITL / Blockers
- [each pending gate by name and what it's waiting on — or "None"]

### Verified vs. assumed
- [what was actually gate-checked this session, and what (if anything) is still unverified]

### Next action
- [single most important next step]
```

If nothing has happened yet this session, say so plainly rather than padding the report.
