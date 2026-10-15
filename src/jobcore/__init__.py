"""jobcore — the single deterministic core for the job-search pipeline.

Pure, testable helpers (no LLM/network): CSV ingest, scoring, quality gates,
PDF rendering, and the shortlist CLI. The native Claude skills orchestrate;
this package is the one place the deterministic logic (and the quality gates)
lives, so it cannot drift.
"""
