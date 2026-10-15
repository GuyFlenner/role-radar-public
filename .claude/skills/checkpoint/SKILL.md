---
name: checkpoint
description: "Saves the current job-search session state to memory and outputs a ready-to-paste resume prompt for continuing in a fresh Claude Code session without losing context. Invoke manually with /checkpoint."
model: "sonnet"
allowed-tools: ["Read", "Bash", "PowerShell", "Write"]
---

# Session Checkpoint

Save session state to persistent memory and produce a ready-to-paste resume prompt so the user can continue in a fresh session without losing context.

**Do not pause to ask for confirmation — gather state, write the file, and output the resume prompt immediately.**

> Adapted for this job-seeking workspace from the upstream `claude-sdlc` checkpoint skill.
> No SDLC phase-gate coupling here — this is a manual, on-demand save.

---

## When invoked

- **Manual** (`/checkpoint`) — the user calls this at any time, on any task (pipeline run, CV tailoring, company research, interview prep, or an ad-hoc task like a document draft).
- **Long-session nudge** — if a session has grown large or is getting sluggish and the user wants a clean handoff before `/clear`.

Arguments may be passed inline as key=value pairs, e.g.:
```
/checkpoint position="willow" phase="cv-align" next="write cover letter, email careers@example.com"
```
If no arguments, infer everything from conversation context and memory.

---

## Steps

### Step 1 — Gather git state

Run one of these (this repo is Windows/PowerShell-primary; Bash also available):

**PowerShell:**
```powershell
$branch = git rev-parse --abbrev-ref HEAD 2>$null; if (-not $branch) { $branch = 'not-a-git-repo' }
Write-Host "BRANCH: $branch"
git log --oneline -5 2>$null
git status --short 2>$null
```

**Bash (Git Bash):**
```bash
echo "BRANCH: $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'not-a-git-repo')"
git log --oneline -5 2>/dev/null || true
git status --short 2>/dev/null || true
```

### Step 2 — Read open items from memory + active positions

1. Read the memory index: `~/.claude/projects/<repo-slug>/memory/MEMORY.md`.
2. Read the pipeline ledger — `uv run python -m src.jobcore.pipeline render`, then the generated
   board — plus any `positions/{slug}/` currently in flight. There is no Active Positions table
   in `CLAUDE.md`: a hand-kept one went three months stale and was replaced by the ledger.

This repo has **no 🔴/🟡 blocker convention** — instead, extract the time-sensitive / in-flight items: positions at INTERVIEW / ON-SITE / APPLIED-awaiting stages, upcoming interview or deadline dates, and anything the current session left unfinished. Limit to the ~5 most relevant to what this session was working on.

### Step 3 — Determine checkpoint context

Resolve the following fields — from inline arguments, then conversation context, then memory:

| Field | How to resolve |
|---|---|
| `focus` | What this session was actually working on (1 line) — e.g. "tailor CV for Willow", "Datadog IMS prep", "hearing statement drafts" |
| `position` | The `positions/{slug}` this relates to, or `n/a` for cross-cutting work |
| `pipeline_phase` | `filter` / `research` / `cv-align` / `interview-prep` / `other` |
| `next_action` | Specific, imperative next step (1–3 lines) |
| `key_decisions` | 2–4 bullets: decisions made this session (fit calls, framing choices, strategy) |
| `open_items` | The in-flight / time-sensitive items from Step 2 relevant to this focus |
| `suggested_skills` | The specific skills the next session should invoke for `next_action` — from `/job-seeking`, `/position-filter`, `/company-researcher`, `/cv-aligner`, `/interview-prep`, `/job-flow-reviewer`, `/architect`, `/diagram-generator`. Name them concretely so the next session doesn't re-derive the toolchain. |

### Step 3b — Redact sensitive data (mandatory before write)

The checkpoint file persists to the memory directory indefinitely, so it must never capture secrets or sensitive personal data. This workspace holds real names, salary figures, HR/legal material, and the "why leaving" narrative — **redact aggressively.** Before writing, scan every resolved field and the resume prompt for:

- **Secrets / credentials** — `ANTHROPIC_API_KEY` and any `*_API_KEY` / `*_SECRET` / `*_TOKEN` value, Bedrock keys, `sk-…` / `AKIA…` / `ghp_…` / `xox[bp]-…` prefixes, passwords, connection strings. Replace the value with `[REDACTED:<kind>]`.
- **Sensitive personal / third-party PII** — names of individuals inside the current employer or a hearing/HR context (CTO, VPs, HR reps) → replace with their **role** (`[CTO]`, `[VP R&D]`, `[HR]`); salary figures / expectations → `[REDACTED:salary]`; personal phone/email/national-ID → `[REDACTED:pii]`; specifics of any legal/hearing/severance matter → summarize and reference the file, don't inline.
- **Fine as-is (project context, not sensitive):** the candidate's own name, and the names of companies/roles being *applied to* (they're public postings). Redaction targets the current-employer internal + legal material, not the public job hunt.

If a value is essential for resuming but sensitive, **reference where it lives** instead of inlining it (e.g. "hearing drafts in `job-tracking/hearing/`"). When in doubt, redact.

### Step 4 — Write checkpoint file

Generate a timestamp `YYYYMMDD_HHMMSS` in **local time (the candidate's local timezone)**. PowerShell: `Get-Date -Format "yyyyMMdd_HHmmss"`. Never use UTC — these filenames are human-anchored.

Write to:
```
~/.claude/projects/<repo-slug>/memory/_checkpoint_<timestamp>.md
```

**Reference, don't duplicate.** The checkpoint is a pointer, not a copy. Link to existing artifacts (`positions/{slug}/…`, `job-tracking/…`, related memory files via `[[name]]`) rather than inlining their contents — inlining produces a stale second copy and bloats the file.

Content:
```markdown
---
name: "_checkpoint_<timestamp>"
description: "Session checkpoint — <focus short name> — <YYYY-MM-DD>"
metadata:
  type: project
---

## Session Checkpoint — <ISO timestamp, the candidate's local timezone>

**Focus**: <focus>
**Position**: <slug | n/a>
**Pipeline phase**: <phase | n/a>
**Branch**: <branch name>

**Next action**: <specific next step>

### Open items
<bullet list of in-flight / time-sensitive items, or "none">

### Key decisions made this session
<2-5 bullet points — fit calls, framing/strategy choices; omit if none>

### Related
<links to positions/, job-tracking/ files, and [[memory-entries]] touched this session>
```

Then add one line to `MEMORY.md`. If a `## Session Checkpoints` section exists, add it there; otherwise append the line to the top bullet list beneath the title:
```
- [Session Checkpoint <YYYY-MM-DD HH:MM>](_checkpoint_<timestamp>.md) — <focus short> (<position | cross-cutting>)
```
Keep the MEMORY.md line under 150 characters.

### Step 5 — Output resume prompt

Print this block to the conversation. Use plain ASCII — no emoji inside the copyable block (they corrupt on paste on some terminals).

```
================================================================
SESSION CHECKPOINT SAVED  --  <YYYY-MM-DD HH:MM IDT>
================================================================

State written to memory. Use the prompt below to continue in a
fresh session without losing context.

--- COPY FROM HERE ---------------------------------------------------

Recall session checkpoint from <YYYY-MM-DD HH:MM IDT> (_checkpoint_<timestamp>.md).

Focus: <focus, 1 line>
Position: <slug or "n/a"> | Pipeline phase: <phase or "n/a"> | Branch: <branch>

Next action:
  <next_action - specific, imperative, 1-3 lines>

Open items:
<bullet list or "  none">

Suggested skills:
  <comma-separated, e.g. /cv-aligner, /interview-prep, /job-flow-reviewer>

Reload the linked checkpoint file, then execute the next action above.

--- COPY TO HERE -----------------------------------------------------

RECOMMENDATION:
  1. Copy the block above (between the dashed lines)
  2. Type /clear  OR  open a new Claude Code session
  3. Paste the copied prompt -- Claude reloads state and continues

You do NOT need to close this session immediately. The checkpoint
is saved regardless. Close only if you are experiencing compaction
or sluggishness.
================================================================
```

---

## Notes

- Never overwrite or delete previous checkpoint files — each is timestamped.
- The resume prompt should stay ~18 lines inside the copy block — scannable and paste-safe. Keep `next_action` to 1–2 lines and `suggested_skills` to one line.
- Do not include code snippets in the resume prompt — file paths and skill names are fine.
- If called multiple times in a session, write a new timestamped file each time (do not merge).
- **RTL note:** if the session produced right-to-left (e.g. Hebrew/Arabic) content, do not paste it from the terminal (it reverses) — reference the saved `.md` file instead so the next session reads it in correct order.
