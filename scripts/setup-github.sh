#!/usr/bin/env bash
# Create the GitHub repo for this platform and push — PRIVATE, always.
#
# This repo is shared between named collaborators and its history must never become public:
# even with personal data kept in $PROFILE_DIR, the skills, gates and fixtures describe how two
# real people run their job searches. There is no --public flag here on purpose.
#
# Usage:
#   bash scripts/setup-github.sh <owner>/<repo> [collaborator-github-username]
#
# Examples:
#   bash scripts/setup-github.sh myuser/role-radar
#   bash scripts/setup-github.sh myuser/role-radar her-github-handle
set -euo pipefail

SLUG="${1:-}"
COLLAB="${2:-}"

if [[ -z "$SLUG" || "$SLUG" != */* ]]; then
  echo "usage: bash scripts/setup-github.sh <owner>/<repo> [collaborator-username]" >&2
  exit 2
fi

cd "$(dirname "$0")/.."

# --- refuse to push a dirty or unsafe tree -----------------------------------
echo "==> Pre-flight"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "    ERROR: working tree is dirty. Commit or stash first." >&2
  git status --short >&2
  exit 1
fi

# The whole point of the split: personal data must not be in the repo we are about to publish.
#
# This check lives in `src/jobcore/publish_check.py`, NOT inline here. It used to be a regex in
# this script and a separate ALLOWLIST in test_profile.py — two answers to one question, and
# they drifted: the shell regex refused a safe push because it could not tell a shipped layout
# template from a real candidate's research. The failure modes are asymmetric, and the dangerous
# direction is the shell copy being the LENIENT one and waving a real CV through.
if ! uv run python -m src.jobcore.publish_check; then
  echo "    ERROR: refusing to create a remote." >&2
  exit 1
fi
echo "    tree clean, no personal data or secrets tracked"

# Prove the privacy guard is live rather than assuming it.
if ! uv run pytest src/jobcore/tests/test_profile.py -q >/dev/null 2>&1; then
  echo "    ERROR: profile/privacy tests fail. Fix before publishing." >&2
  exit 1
fi
echo "    privacy tests pass"

# --- create the remote --------------------------------------------------------
if ! command -v gh >/dev/null 2>&1; then
  cat <<EOF

  'gh' (GitHub CLI) is not installed, so this script cannot create the repo for you.
  Do it manually — it is three commands:

    1. Create a PRIVATE repo named '$SLUG' at https://github.com/new
       (Private. Do NOT initialise with a README, .gitignore or licence.)

    2. git remote add origin https://github.com/$SLUG.git
       git push -u origin main

    3. Add your collaborator:
       https://github.com/$SLUG/settings/access

  Everything above already passed the pre-flight checks, so the tree is safe to push.
EOF
  exit 0
fi

echo "==> Creating PRIVATE repo $SLUG"
if gh repo view "$SLUG" >/dev/null 2>&1; then
  echo "    already exists — reusing"
else
  gh repo create "$SLUG" --private --source=. --remote=origin --description "Private job-search platform (profile-driven, two users)"
fi

# Belt and braces: confirm GitHub actually reports it private before pushing.
VIS=$(gh repo view "$SLUG" --json visibility --jq .visibility 2>/dev/null || echo UNKNOWN)
if [[ "$VIS" != "PRIVATE" ]]; then
  echo "    ERROR: repo visibility is '$VIS', expected PRIVATE. Not pushing." >&2
  exit 1
fi
echo "    visibility confirmed: PRIVATE"

git remote get-url origin >/dev/null 2>&1 || git remote add origin "https://github.com/$SLUG.git"
git push -u origin main
echo "    pushed"

if [[ -n "$COLLAB" ]]; then
  echo "==> Inviting $COLLAB"
  gh api -X PUT "repos/$SLUG/collaborators/$COLLAB" -f permission=push >/dev/null
  echo "    invited with write access"
fi

cat <<EOF

Done. https://github.com/$SLUG  (private)

Tell your collaborator to:
  git clone https://github.com/$SLUG.git
  cd \$(basename "$SLUG")
  uv sync && uv run pytest -q
  cp profiles/_template.json ~/job-search/profile.json    # her own, outside the repo
  echo 'PROFILE_DIR=~/job-search' >> .env
  cp .mcp.json.example .mcp.json                          # add her own Exa key
  uv run python -m src.jobcore.profile validate

Her CV, positions and pipeline stay in her \$PROFILE_DIR and never reach this repo.
See SETUP.md.
EOF
