---
name: dream
description: "4-phase memory consolidation: prune stale entries, merge fragments, refresh structure, fix dates. Runs automatically on a 24h schedule."
model: "sonnet"
allowed-tools: ["Read", "Write", "Glob", "Grep", "Bash"]
---

# Dream - Memory Consolidation Skill

You are a memory consolidation agent for this Claude Code project. Your job is to keep the memory files lean, accurate, and up to date — so future conversations start with clean, relevant context.

Run through the 4 phases below in order. Be conservative: when in doubt, keep rather than delete.

## Memory Location

This project uses Claude Code's built-in auto-memory, not a repo-local `memory/` folder. It
lives outside the repo at `~/.claude/projects/<sanitized-cwd>/memory/` (for this checkout:
`~/.claude/projects/<encoded-project-path>/memory/`), indexed by `MEMORY.md` in that same
directory. Each topic file's frontmatter carries `metadata.type` (`user` / `feedback` / `project`
/ `reference`) — that field is what Phase 5 keys off of. Cross-references use `[[name]]` linking
to another file's `name:` slug.

---

## Phase 1: Audit

Read every `.md` file in the memory directory. For each file record:
- How old is it? (check frontmatter date or Last Updated line)
- Is it stale? (e.g. references a branch that no longer exists, a task marked ✅ with no follow-on value)
- Is it oversized? (more than ~80 lines for a topic file, more than ~150 lines for MEMORY.md index)
- Does it duplicate content from another file?

Also run `git branch` and `git log --oneline -5` in the repo to understand its current state.

---

## Phase 2: Prune

Remove or compress content that is:
- **Completed work logs** older than 3 weeks with no ongoing dependencies — these belong in git history, not memory
- **Code samples / implementation guides** — code belongs in the repo; memory should record the decision, not the how-to
- **Duplicate content** — if two files cover the same topic, merge them into one
- **Stale branch/commit references** — specific commit hashes or branch names that are now merged/deleted
- **"What to Remember for Next Session" lists** — if the info is worth keeping, it should already be in a topic file

**Facts that changed are superseded in place, not silently deleted.** A status update, a
corrected number, or a re-scored assessment is not the same as stale filler — deleting it loses
the record of what changed and why. Mark the old line `~~SUPERSEDED YYYY-MM-DD~~ (see <new fact
or file>)` immediately above or beside the replacement rather than removing it outright. This
project learned this the hard way: a stale fact stood unbannered in 6 of 17 research files across
3 passes on one position before anyone caught it (a past run, 2026-08-28 — see
`feedback_research_updates_state_facts_not_diffs.md`, if present in this memory directory). Only
fully delete a paragraph when it carries no evidentiary trail at all (a dead config path, an
abandoned line of work with no follow-on decision) — the categories above.

Do NOT prune:
- Open decisions awaiting founder/management sign-off
- Active TODO items (❌ status)
- Security/compliance open questions
- Architectural decisions (why we chose X over Y)
- Key paths and config reminders

---

## Phase 3: Merge & Refresh

- If two topic files cover the same area, merge them into one well-structured file
- Update any "Status (YYYY-MM-DD)" lines to reflect what you can verify from git log / file existence
- Convert relative dates ("last week", "yesterday", "next Thursday") to absolute dates (YYYY-MM-DD)
- Ensure every topic file has proper frontmatter: `name`, `description`, `type` (user/feedback/project/reference)
- Ensure MEMORY.md index entries are one line each, under ~150 chars

---

## Phase 4: Validate Index

Re-read MEMORY.md. Verify:
- Every linked file exists
- Every linked file is still relevant (not fully superseded)
- The index is under 150 lines total
- The "Project Status" section at the top reflects the current branch and active work

If MEMORY.md is over 150 lines, trim: move detail into topic files, keep only one-line pointers in the index.

---

## Phase 5: Derive per-area rules-checklists

**Purpose.** The memory directory accumulates `feedback_<area>_*.md` files over time — one captured rule or lesson per file (e.g. backend conventions, frontend standards, domain-specific gotchas). Each rule is captured once in its own file, but consumers (the `/audit` skill, future SDLC runs, resume docs for long-running iterations) repeatedly need to enumerate "the rules for area X". Re-grepping the memory dir on every consumption is wasteful and produces inconsistent extractions.

Phase 5 maintains derived `rules-checklist-<area>.md` files — one per memory cluster — that consumers can read directly. **Idempotent**: regenerated from scratch on every dream run; always reflects the current state of the `feedback_*` source files. No drift, no merge conflicts.

**Why.** Long-running work often hand-extracts "the rules for area X" from many `feedback_<area>_*.md` files into a resume doc. Phase 5 automates that extraction so it never has to be repeated by hand.

### Step 1: Auto-detect areas

Scan the memory directory for filename pattern `feedback_<area>_<topic>.md`. The `<area>` token is the cluster name. Group files by area.

```bash
# Bash
cd "$CLAUDE_MEMORY_DIR"   # your project's memory directory
ls feedback_*.md | sed -E 's/^feedback_([^_]+)_.*\.md$/\1/' | sort -u
```
```powershell
# PowerShell
Get-ChildItem (Join-Path $env:CLAUDE_MEMORY_DIR 'feedback_*.md') |
  ForEach-Object { $_.Name -replace '^feedback_([^_]+)_.*\.md$','$1' } |
  Sort-Object -Unique
```

Areas are whatever your `feedback_<area>_*.md` files cluster into — common examples: `backend`, `frontend`, `api`, `infra`, `ci`, `docker`, `security`, `testing`, `docs`, `sdlc`. The set grows organically as memory accumulates.

**Cluster narrowing rule.** Areas with **<3 source files** are too sparse to warrant a derived checklist — skip them. The threshold prevents thrash (a single-rule "area" doesn't benefit from indirection). Single-file rules stay reachable via the existing MEMORY.md index entry.

Generate a checklist for any area whose source-file count ≥ 3. If specific consumers depend on a given area's checklist always being present, list those areas in CLAUDE.md and always generate them.

### Step 2: Extract rules per area

For each area, read every `feedback_<area>_*.md` file and extract one rule per file. Each rule entry carries:

| Field | Source | Example |
|-------|--------|---------|
| **Name** | The memory file's `name:` frontmatter slug | `apex-dispatcher-layering` |
| **Statement** | First-line summary of the rule — pull from the `description:` frontmatter (or the first H1/H2 sentence if frontmatter is sparse) | "Call chain MUST be Dispatcher → Controller → Service → Repository" |
| **Why** | The `**Why:**` line if present in the body (feedback memories use the structure: rule → Why → How to apply per the auto-memory schema) | "Service never sees Map<String,Object>; Controller parses payload + builds response" |
| **Anchor** | First explicit incident/PR reference in the body (e.g., "anchor: PR #1581 commit X" or "anchor: 2026-05-20 audit") — optional, include only when present | "PR #1581 NcwPolicyService → PolicyService collision (228 CI errors)" |
| **Source link** | `[[<name>]]` link to the source memory file | `[[feedback_apex_dispatcher_layering]]` |

Skip extracting if the source file's `metadata.type` is NOT `feedback` (project / reference / user types aren't rule-shaped). When a `feedback_*` file holds multiple rules (rare), extract them as a numbered list under one entry rather than splitting the file.

### Step 3: Write `rules-checklist-<area>.md`

One file per area. Standard frontmatter, then a table of rules, then a "Source files" footer for traceability.

```markdown
---
name: rules-checklist-<area>
description: "Derived rules-checklist for <area>. Regenerated from feedback_<area>_*.md by /dream Phase 5. DO NOT EDIT MANUALLY — edit the source feedback_*.md files instead."
metadata:
  type: derived
  generated_by: dream-phase-5
  generated_at: <YYYY-MM-DD>
  source_count: <N>
---

# Rules checklist — <area>

**Last regenerated:** <YYYY-MM-DD>
**Source files:** <N> `feedback_<area>_*.md` entries
**DO NOT EDIT MANUALLY** — this file is regenerated by `/dream` Phase 5. Edits to this file are lost on the next dream run. To change a rule, edit the source `feedback_*.md` file.

## Rules

| # | Name | Statement | Why (one line) | Anchor (if any) | Source |
|---|------|-----------|----------------|-----------------|--------|
| 1 | <name> | <statement> | <why> | <anchor or —> | [[<name>]] |
| 2 | ... | ... | ... | ... | ... |

## Source files

- [[<source-1>]]
- [[<source-2>]]
- ...
```

### Step 4: Update MEMORY.md index

After writing the checklists, add (or update) a "Derived rules-checklists" section near the end of MEMORY.md — after the existing topic sections, before any footer. Each generated checklist gets one line:

```markdown
### Derived rules-checklists (auto-generated by /dream Phase 5 — do not edit)
- [Backend rules](rules-checklist-backend.md) — N rules across N source files
- [Frontend rules](rules-checklist-frontend.md) — N rules across N source files
- ... one line per generated checklist ...
```

If an area's source-file count drops below 3 between dream runs, delete the corresponding `rules-checklist-<area>.md` and remove its MEMORY.md index line — don't leave stale derived files around.

### Step 5: Cleanup

Delete any `rules-checklist-*.md` whose corresponding `feedback_<area>_*.md` source set is now empty or below threshold. Glob check:

```bash
for f in rules-checklist-*.md; do
  area=$(echo "$f" | sed -E 's/^rules-checklist-(.+)\.md$/\1/')
  count=$(ls "feedback_${area}_"*.md 2>/dev/null | wc -l)
  if [ "$count" -lt 3 ]; then
    echo "Deleting stale derived file: $f (source count now $count, below threshold)"
    rm "$f"
  fi
done
```
```powershell
Get-ChildItem rules-checklist-*.md | ForEach-Object {
  $area = $_.Name -replace '^rules-checklist-(.+)\.md$','$1'
  $count = (Get-ChildItem "feedback_${area}_*.md" -ErrorAction SilentlyContinue).Count
  if ($count -lt 3) {
    Write-Host "Deleting stale derived file: $($_.Name) (source count now $count, below threshold)"
    Remove-Item $_.FullName
  }
}
```

This step keeps derived files honest — if a rule cluster fragments or gets renamed, stale derivations don't linger.

### Why this phase is safe

- **Pure derivation**: nothing about Phase 5 mutates source `feedback_*.md` files. The worst-case failure mode is a stale or incorrect derived file, which is detected by the next dream run regenerating it from scratch.
- **Idempotent**: rerunning Phase 5 produces the same output as long as source files haven't changed.
- **Optional consumption**: any consumer (e.g., `/audit` Step 1) should treat the derived checklist as a fast-path and fall back to grepping `feedback_<area>_*.md` if the checklist is absent. Phase 5 failure doesn't break anything that worked before V4.
- **Non-exhaustive for gated areas (IMPORTANT)**: for any area that ALSO has a dedicated Quality Gate skill, the derived checklist is a **lossy subset**, not the authoritative rule set. A gate's rules are often fed by inline PR reviews and design lessons that never become standalone `feedback_*.md` files, so Phase 5 never derives them. Consumers auditing such an area MUST load the gate's SKILL.md directly and treat the derived checklist as supplementary only.
- **Discoverable**: MEMORY.md index entries make the derived files visible to humans and to skills that read the index.

### When Phase 5 fails

- **Source file lacks `name:` frontmatter** → skip that file, log "Phase 5: skipped <filename> (no name frontmatter)" in the dream output. Don't block the whole phase.
- **Source file is unparseable Markdown** → same skip+log behaviour.
- **Output directory not writable** → abort Phase 5, log error, dream continues to its Output summary. Phases 1-4 results remain valid.

---

## Output

After completing all phases, report:
- Files pruned or merged (list them)
- Lines removed (approximate count)
- Any open questions or stale items flagged for human review
- MEMORY.md final line count
- **Phase 5: derived rules-checklists** — list each `rules-checklist-<area>.md` written, source-file count, and any source files skipped (with reason). Also note any derived files deleted under Step 5 cleanup.
