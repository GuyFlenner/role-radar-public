# Contributing

1. `uv sync && bash scripts/verify_all.sh` must be green before and after your change (all 8 gates).
2. **No personal data, ever** — real names, employers, emails, salaries or CVs. Fixtures in
   `profiles/_fixtures/` are synthetic; a test enforces it.
3. A bug that slipped past a gate gets a regression test **and** a hardened gate, not just a fix.
4. "A skipped gate is not a green gate" — never report a partial run as passing.
5. Keep the deterministic core (`src/jobcore/`) free of LLM calls; skills orchestrate, jobcore computes.

Open a pull request against `main`; CI runs the same contract.
