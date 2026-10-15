# Setup — first run on a clean machine

This repo is the **platform**. Your CV, your pipeline and your job-search notes are **not** in it
and must never be committed to it — see [Why your data lives outside](#why-your-data-lives-outside).

Time to first ranked shortlist: about 15 minutes.

---

## 1. Prerequisites

- **Python 3.13** and [**uv**](https://docs.astral.sh/uv/) (`pip install uv` or the standalone installer)
- **Claude Code** if you want the interactive skills (the deterministic CLI works without it)

```bash
git clone <this repo>
cd role-radar
uv sync
uv run pytest -q          # expect: all pass. If not, stop here and fix it first.
```

## 2. Create your data directory — outside this repo

```bash
mkdir -p ~/job-search            # anywhere you like, just NOT inside this clone
```

Put your master CV there as `cv.md`. It needs a level-1 heading with your name, because the PDF
filename is derived from it:

```markdown
# Jane Smith

**Senior Frontend Engineer**

## SUMMARY
...
```

## 3. Create your profile

```bash
cp profiles/_template.json ~/job-search/profile.json
$EDITOR ~/job-search/profile.json
```

The template documents every field inline. The five that change your results most:

| Field | Why it matters |
|---|---|
| `skills.keywords` | **the main ranking signal** — your actual stack. Empty means every role scores the same. |
| `roles.target_titles` | titles you want. A role matching one is always considered relevant. |
| `roles.require_leadership` | **`false` if you are an IC.** `true` filters to lead/manager roles only — leave it `true` by mistake and every IC role scores 1. |
| `geo.mode` | `preferred` ranks by location; `required` **drops** anything outside your list. Start with `preferred`. |
| `facts` | ground truth for the honesty gates. Leave it empty and those gates **skip** — see below. |

Then point the platform at it and check it:

```bash
echo 'PROFILE_DIR=~/job-search' >> .env      # .env is gitignored
uv run python -m src.jobcore.profile validate
uv run python -m src.jobcore.profile --brief
```

`validate` exits non-zero and tells you what is wrong. It catches the two misconfigurations that
silently produce garbage: `geo.mode: "required"` with no locations (drops every role), and empty
`skills.keywords` (flattens every score).

## 4. Job boards and Exa (the discovery tiers)

```bash
cp ats_targets.example.json ats_targets.json   # gitignored; your target company boards
cp .mcp.json.example .mcp.json                 # gitignored; add YOUR OWN Exa API key
```

`ats_targets.json` drives Tiers 1–2 (Greenhouse / Comeet / LinkedIn guest) and costs no tokens.
`.mcp.json` wires the **Exa** MCP server, which `job-radar deep` uses as Tier 3 to reach
careers-only postings. Both hold private data — board tokens and an API key — so both are
gitignored and each collaborator supplies their own.

## 4b. Optional — legacy

```bash
cp ats_targets.example.json ats_targets.json   # gitignored; holds your target companies
```

## 5. Publish to GitHub (private)

```bash
bash scripts/setup-github.sh <owner>/<repo> [collaborator-username]
```

It refuses to create the remote if the tree is dirty, if any personal data or secret is tracked,
or if the privacy tests fail — and it re-reads the visibility back from GitHub and aborts unless
it is `PRIVATE`. There is no public option.

## 6. Run it

```bash
uv run python -m src.jobcore.profile --brief          # confirm it's you
uv run python -m src.jobcore.ats_shortlist --help     # poll job boards
uv run python -m src.jobcore.check <position-dir>     # run the CV quality gates
bash scripts/verify_all.sh                            # the full CI contract
```

---

## Why your data lives outside

Two people share this repo. If personal data were committed here, each of you would permanently
see the other's CV, salary bands, rejections and candid interview self-assessments. That is not a
policy you can maintain by being careful — so the split is structural:

- **this repo** — engine, gates, skills, profile schema, synthetic fixtures. Shared, reviewed together.
- **`$PROFILE_DIR`** — your CV, `positions/`, `job-tracking/`, your `profile.json`. Yours alone.

`.gitignore` blocks the obvious paths and a test (`test_no_profile_dir_content_is_tracked_by_git`)
fails the build if personal data is ever staged. Back up `$PROFILE_DIR` separately — it can be its
own private git repo.

## The one thing worth understanding: skipped gates

Several quality gates judge a CV against **your** history (`facts` in the profile): terms you must
never claim, your exact credential, and which achievements belong to which employer.

**With no `facts` block, those gates cannot run — and they say so:**

```
SKIPPED[cv-facts]: profile has no `facts` block — candidate-specific honesty checks did NOT run
```

That is deliberate. Returning "no violations" when nothing was checked would read as *your CV is
honest*, which is a far more expensive lie than an inconvenient warning. **A skipped gate is not a
green gate.** Fill in `facts` when you want the honesty checks to mean something.

## Working on the platform together

- Personal data changes go in `$PROFILE_DIR` and never generate a commit here.
- Platform changes are normal PRs.
- **The test suite runs against two synthetic profiles** (`profiles/_fixtures/`) — a backend lead
  with a hard commute band, and a frontend IC in Berlin. This is what stops a change that improves
  one person's ranking from quietly wrecking the other's. If you tune scoring, both must stay green.
- Never put a real name, employer or salary in `profiles/_fixtures/` — a test enforces that too.
