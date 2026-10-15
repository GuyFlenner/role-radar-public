# role-radar — Claude Code configuration

Profile-driven job-search platform. **Agent-friendly: external users clone it and run `use onboard` (see `AGENTS.md`).**

Read `SETUP.md` for first-run. This file is the operating contract for Claude Code sessions.

---

## 👤 The profile is the candidate — read it before anything else

```bash
uv run python -m src.jobcore.profile --brief
```

Everything career-shaped lives in one JSON file resolved from `$PROFILE_DIR` (which sits
**outside this repo**): target titles, keywords, geography, seniority band, salary, and the
`facts` block the honesty gates judge against.

**Never assume an employer, technology, language, location, seniority or currency that is not in
that output.** This repo is shared; hardcoding one person's history into code or a prompt is the
specific defect this platform was built to remove — `scoring.py` and `gates.py` used to encode
one candidate's career, and a second user's *true* claims were reported as lies.

If the brief says `facts … NOT CONFIGURED`, the honesty gates **cannot run**. Say so; do not
imply a CV was fact-checked.

---

## 🔒 Privacy model — non-negotiable

| Lives here (shared, committed) | Lives in `$PROFILE_DIR` (private, gitignored) |
|---|---|
| `src/jobcore/`, `.claude/skills/`, `scripts/`, tests | your `profile.json`, `cv.md` |
| `profiles/_template.json`, `profiles/_fixtures/` | `positions/`, `job-tracking/` |
| docs, CI, schema | anything naming a real company you are pursuing |

Rules:
1. **Never commit personal data here.** A shared private repo is not isolation — anything you
   commit is permanently visible to every collaborator. `.gitignore` blocks the obvious paths and
   `test_no_profile_dir_content_is_tracked_by_git` fails the build if any slips through.
2. **Never put a real name, employer, salary or email in `profiles/_fixtures/`** — those are
   synthetic, and a test enforces it.
3. **Secrets** (`.env`, `.mcp.json`, `ats_targets.json`) are gitignored. Each collaborator
   supplies their own; the repo ships only `*.example` versions.
4. **Never read a directory the user has marked private** (legal, HR, medical) and never pull
   such content into generated text, even if it appears in auto-loaded memory.

---

## ✅ A skipped gate is not a green gate

The single most important rule in this repo, and the one that has been violated most.

Several gates judge against `profile.facts`. With no facts configured they emit
`SKIPPED[...]` rather than an empty list — because "no violations found" when nothing was
checked reads as *this CV is honest*, which is a far more expensive lie than a warning.

The same applies elsewhere: `src.jobcore.check` reporting `cov=n/a` means the ATS-coverage gate
was **skipped, not passed** (a position without a committed `gate_terms.txt` has no ATS
verification at all). Never report a skipped gate as green. Never report a partial run as green.

**Before claiming anything passes:**

```bash
bash scripts/verify_all.sh    # ruff check · ruff format --check · pytest · publish check ·
                              # drawio diagrams · skill lint · rag goldens · mypy
                              # EIGHT blocking gates, no advisory ones. A summary missing one of
                              # those lines means that gate did not run — which is not a pass.
```

Run it on the final committed state. Editing after verifying invalidates the check.

**Prove a guard by making it refuse.** A guard you have never seen fail is not verified.

---

## 🔁 Keeping parity with an upstream repo

If this platform was extracted from, or is synced with, another repo:

```bash
uv run python scripts/check_parity.py ../that-repo
```

Two things were silently dropped during the original extraction and found only by accident:
`.mcp.json` (so Exa's discovery tier was dead) and the whole `src/jobcore/rag/` package. Neither
failed a test, because the tests came across too. **A copy's own success is not proof the copy
was complete** — run the parity check after any sync.

---

## Tech stack

- **Python 3.13**, **uv** (`uv sync`; `uv add <pkg>` — never `pip install`)
- **`src/jobcore/`** — the deterministic core. No LLM calls: profile, scoring, gates, ATS
  providers, liveness, pipeline ledger, PDF, drawio lint. This is where correctness lives.
- **`.claude/skills/`** — the interactive layer. Skills orchestrate; they must not re-implement
  logic that belongs in `jobcore`.
- **`src/jobcore/rag/`** — retrieval over your own notes. Corpus path is `rag.corpus_globs` in
  the profile, so your notes stay outside this repo.

## Common commands

```bash
uv run python -m src.jobcore.profile validate|show|--brief
uv run python -m src.jobcore.ats_shortlist --config ats_targets.json      # poll job boards
uv run python -m src.jobcore.pipeline render|validate                      # the opportunity ledger
uv run python -m src.jobcore.check <position-dir>                          # CV quality gates
uv run python -m src.jobcore.drawio_lint <file.drawio>                     # one diagram
uv run python -m src.jobcore.diagram_gate                                   # every tracked .drawio
uv run python -m src.jobcore.skill_lint                                    # skill prose vs reality
uv run python -m src.jobcore.rag eval|query "<text>"                       # retrieval gate / ad-hoc
uv run python -m src.jobcore.usage [--by-turn] [--gate]                    # session cost + stop advice
uv run python -m src.jobcore.smoke                                         # probe the LLM fallback chain
bash scripts/capped.sh 60 <command>                                        # cap noisy output at N lines
bash scripts/verify_all.sh                                                 # the CI contract
bash scripts/setup-github.sh <owner>/<repo> [collaborator]                 # private repo + push
```

## Tooling that ships but is easy to miss

Everything here is in the repo and working. It is listed because an audit found the
documentation had been trimmed while the tooling stayed — capability present, undiscoverable,
which is how a team reinvents what it already owns.

| Thing | What it is |
|---|---|
| `.claude/agents/` | Three subagents: `gate-runner` (runs the gate suite), `liveness-prober` (batch URL/board probes), `repo-surveyor` (read-only git analysis). Delegate work whose *output* you need but whose *process noise* you don't — a subagent's tool churn never enters your context. |
| `src/jobcore/usage.py` | Measures what this session actually cost. `--gate` exits 0/1/2 (OK / compact soon / stop and start fresh). Cost is driven by round-trips × context size, not reply length. |
| `scripts/capped.sh` | `bash scripts/capped.sh 60 uv run pytest` — last 60 lines plus a truncation count. A tool result joins the context prefix and is re-read on every later request, so a 300-line log early in a session is paid hundreds of times. |
| `scripts/sync-claude-skills.*` | Pulls skill updates from an upstream framework repo per `claude-skills.lock`. `-DryRun` first. |
| `scripts/check_parity.py` | One-off, not a gate — see its docstring. Run after syncing infra from another repo. |
| `src/jobcore/skill_lint.py` | Blocking gate: skill prose must reference config and paths that exist. |
| `docs/architecture/` | Five-page draw.io architecture diagram. **Page 0 is the whiteboard page** — 8 boxes, the one to memorise before presenting. `diagram_gate` lints it in CI. |

---

## Shell conventions

**The Bash tool is Git Bash (POSIX sh), not PowerShell.** Never use PowerShell here-strings
(`@'…'@`) or `$env:VAR` in it. Multi-line commit messages use a heredoc:

```bash
git commit -F - <<'EOF'
type(scope): subject

body
EOF
```

Also: `export GIT_PAGER=cat` (a paginated git command with no TTY hangs until timeout), and on
Windows avoid writing `$HOME`-derived paths into `.env` — Git Bash emits `/c/Users/...`, which
Python resolves to `C:\c\Users\...`. The profile loader translates it, but prefer a real path.

## Working on this platform with someone else

- Personal-data changes happen in `$PROFILE_DIR` and never produce a commit here.
- Platform changes are normal PRs.
- **The test suite runs against two synthetic profiles** — a backend lead with a hard commute
  band, and a frontend IC in Berlin. That is what stops a change improving one person's ranking
  from quietly wrecking the other's. If you touch scoring or gates, both must stay green.
