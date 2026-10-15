---
description: Poll the public ATS boards in ats_targets.json for new matching roles (on-demand alternative to the Referally CSV)
argument-hint: "[full] [anywhere] — omit = new since last poll, commute-band only; 'full' = all matches; 'anywhere' = drop the geo filter"
allowed-tools: Bash, Read
---

> **Prefer `use job-radar`** for the full experience — it wraps this poll and adds LinkedIn
> coverage of employers whose roles a public ATS under-serves, optional Exa discovery, and a liveness sweep over your
> tracked pursuits. This command remains the thin CLI-only path.

Run the on-demand ATS job poll and present the results. Arguments: `$ARGUMENTS`

## Steps

1. **Pick the mode from the arguments:**
   - If `$ARGUMENTS` contains `full`, `all`, or `scratch` → **full mode**: add `--from-scratch` (show every current match, ignore the delta store).
   - Otherwise → **delta mode** (default): only roles that are new since the last poll.
   - If `$ARGUMENTS` contains `anywhere`, `global`, or `worldwide` → add `--anywhere` (disable the geo filter). Otherwise the poll is **gated to the profile's `geo` block** — `geo.mode: required` drops out-of-band roles outright (global boards return roles from everywhere), `preferred` keeps them and flags location as a ranking signal. Read `uv run python -m src.jobcore.profile --brief` before describing the band; do not assume a country.

2. **Run the poller** (liveness is on by default — CLOSED postings are dropped, UNVERIFIED are flagged):

   ```bash
   uv run python -m src.jobcore.ats_shortlist --config ats_targets.json \
     --out-json positions/_ats_shortlist.json \
     --out-md job-tracking/ats_shortlist_ranked.md
   ```
   Append `--from-scratch` if full mode. If the run reports "No positions" in delta mode, tell the user there's nothing new since the last poll and stop.

3. **Read `positions/_ats_shortlist.json`** and present a focused digest — do NOT dump the whole list:
   - By default every row already satisfies the profile's geo gate (the CLI applies it), so rank by `match_score` desc. Under `--anywhere` the gate is off, so group in-band rows first, then the rest.
   - Call out first: any `match_score >= 8` (AI/eng-leadership), and any at a company with an active pursuit — check the ledger, which is the only source of
     pipeline truth: `uv run python -m src.jobcore.pipeline render`, then read the generated board.
   - For each shown role: `score/10 · Company · Title · Location · liveness · source`. Flag `UNVERIFIED` roles as "open the link to confirm."
   - End with a one-line tally: N new/total matches, M in the profile's preferred locations, and how many were dropped as CLOSED (compare tool stderr if useful).

4. **Do not** commit anything or edit `ats_targets.json`. This is read-only reconnaissance. If the user wants a new company added, tell them to add it to `ats_targets.json` (or ask me to find its board token).

## Notes
- Delta mode advances the seen-store, so repeated `/poll-jobs` shows only what's newly appeared. Use `/poll-jobs full` to re-review everything.
- To widen coverage, more boards can be added to `ats_targets.json` (Greenhouse token or Comeet uid+token).
