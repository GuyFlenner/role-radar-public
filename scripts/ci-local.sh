#!/bin/sh
# Run the EXACT blocking steps from .github/workflows/ci.yml locally.
# Use before pushing (the pre-push hook runs these automatically; this is the
# manual on-demand equivalent). mypy is advisory in CI, so shown but non-blocking.
set -e

echo "[ci-local] ruff check ."          && uv run ruff check .
echo "[ci-local] ruff format --check ." && uv run ruff format --check .
echo "[ci-local] pytest"                && uv run python -m pytest -q
echo "[ci-local] mypy (advisory)"       && (uv run mypy src/jobcore || echo "[ci-local] mypy warnings — non-blocking, matches CI")
echo "[ci-local] OK — mirrors CI blocking steps"
