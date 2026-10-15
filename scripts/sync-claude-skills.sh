#!/usr/bin/env bash
# Sync skills from claude-sdlc into the current project, governed by claude-skills.lock.
# See docs/skills-sharing.md for the convention this script implements.
#
# Copies the ENTIRE skill directory (SKILL.md + companion files such as rules.md,
# references/) from the upstream repo's *git content* at a pinned commit — never from
# the working tree — and records an integrity hash (content_sha256) per skill.
#
# Usage:
#   ./scripts/sync-claude-skills.sh                       # apply (at upstream HEAD)
#   ./scripts/sync-claude-skills.sh --dry-run             # show what would change
#   ./scripts/sync-claude-skills.sh --at <sha>            # sync from a specific commit
#   ./scripts/sync-claude-skills.sh --force               # overwrite local_modifications=true skills
#                                                         # AND ignore a dirty upstream .claude/skills/
#   ./scripts/sync-claude-skills.sh --no-claude-md-check  # skip CLAUDE.md skills-table audit
#
# Requires: jq, tar

set -euo pipefail

LOCK_FILE="claude-skills.lock"
DRY_RUN=0
FORCE=0
NO_CLAUDE_MD_CHECK=0
AT="HEAD"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)              DRY_RUN=1 ;;
    --force)                FORCE=1 ;;
    --no-claude-md-check)   NO_CLAUDE_MD_CHECK=1 ;;
    --lock=*)               LOCK_FILE="${1#*=}" ;;
    --at=*)                 AT="${1#*=}" ;;
    --at)                   shift; AT="${1:?--at requires a commit-ish argument}" ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \?//'
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
  shift
done

if [ ! -f "$LOCK_FILE" ]; then
  echo "Lock file not found: $LOCK_FILE (cwd: $PWD)" >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "This script requires jq. Install via your package manager." >&2
  exit 1
fi

# jq on Windows may emit CRLF — always strip CRs from captured values
jqr() { jq -r "$@" | tr -d '\r'; }

SOURCE_DIR=$(jqr '.source_local_path' "$LOCK_FILE")
DEFAULT_BRANCH=$(jqr '.default_branch' "$LOCK_FILE")

if [ ! -d "$SOURCE_DIR" ]; then
  echo "Source repo not found at '$SOURCE_DIR'. Update source_local_path in $LOCK_FILE." >&2
  exit 1
fi

# Resolve the commit to sync from (pinned; never the working tree)
if ! SYNC_SHA=$(git -C "$SOURCE_DIR" rev-parse --verify --quiet "${AT}^{commit}"); then
  echo "Cannot resolve '--at $AT' to a commit in $SOURCE_DIR." >&2
  exit 1
fi
SOURCE_BRANCH=$(git -C "$SOURCE_DIR" rev-parse --abbrev-ref HEAD)

if [ "$SOURCE_BRANCH" != "$DEFAULT_BRANCH" ]; then
  echo "WARNING: Source repo is on branch '$SOURCE_BRANCH', expected '$DEFAULT_BRANCH'. Continuing -- verify this is intentional." >&2
fi

# Refuse to run against a dirty upstream skills tree: the copy comes from git content,
# so uncommitted upstream edits would be silently ignored — surface that instead.
DIRTY=$(git -C "$SOURCE_DIR" status --porcelain -- .claude/skills 2>/dev/null || true)
if [ -n "$DIRTY" ]; then
  if [ "$FORCE" -eq 1 ]; then
    echo "WARNING: upstream .claude/skills/ has uncommitted changes; --force given — syncing from committed content at $SYNC_SHA (uncommitted edits are NOT copied)." >&2
  else
    echo "ERROR: upstream repo '$SOURCE_DIR' has uncommitted changes under .claude/skills/." >&2
    echo "Sync copies committed git content only, so those edits would be silently skipped." >&2
    echo "Commit (or stash) them upstream, or re-run with --force to sync from $SYNC_SHA anyway." >&2
    exit 1
  fi
fi

NOW_ISO=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
UPDATED=()
SKIPPED=()
MISSING=()

# Deterministic content hash over a skill directory:
# files sorted bytewise (LC_ALL=C) by relative path (forward slashes);
# digest input per file = <relative-path> + LF + <raw file bytes>.
hash_skill_dir() {
  local dir="$1"
  (
    cd "$dir"
    find . -type f | sed 's|^\./||' | LC_ALL=C sort | while IFS= read -r rel; do
      printf '%s\n' "$rel"
      cat "$rel"
    done
  ) | sha256sum | awk '{print $1}'
}

# Read skill names
SKILL_NAMES=$(jqr '.skills | keys[]' "$LOCK_FILE")

# Build a temp working copy of the lock file
TMP_LOCK=$(mktemp)
cp "$LOCK_FILE" "$TMP_LOCK"
trap "rm -f '$TMP_LOCK'" EXIT

for skill in $SKILL_NAMES; do
  src_path=".claude/skills/$skill"
  dst_dir=".claude/skills/$skill"

  if ! git -C "$SOURCE_DIR" cat-file -e "$SYNC_SHA:$src_path/SKILL.md" 2>/dev/null; then
    echo "[MISSING] $skill -- $src_path/SKILL.md not found at $SYNC_SHA in $SOURCE_DIR"
    MISSING+=("$skill")
    continue
  fi

  local_mod=$(jqr --arg s "$skill" '.skills[$s].local_modifications' "$LOCK_FILE")
  if [ "$local_mod" = "true" ] && [ "$FORCE" -eq 0 ]; then
    reason=$(jqr --arg s "$skill" '.skills[$s].modification_reason // "(no reason recorded)"' "$LOCK_FILE")
    echo "[SKIP-LOCAL] $skill -- local_modifications=true. Reason: $reason"
    SKIPPED+=("$skill")
    continue
  fi

  if [ "$DRY_RUN" -eq 1 ]; then
    echo "[DRY] Would copy (from $SYNC_SHA):"
    git -C "$SOURCE_DIR" ls-tree -r --name-only "$SYNC_SHA" -- "$src_path" | sed "s|^|[DRY]   |"
    UPDATED+=("$skill")
    continue
  fi

  # Replace the destination directory with the upstream git content (whole dir,
  # so companion files removed upstream don't linger downstream).
  rm -rf "$dst_dir"
  mkdir -p "$dst_dir"
  git -C "$SOURCE_DIR" archive "$SYNC_SHA" "$src_path" | tar -x --strip-components=3 -C "$dst_dir"

  content_hash=$(hash_skill_dir "$dst_dir")

  # Update lock entry
  jq --arg s "$skill" --arg c "$SYNC_SHA" --arg t "$NOW_ISO" --arg h "$content_hash" \
    '.skills[$s].synced_from_commit = $c | .skills[$s].synced_at = $t | .skills[$s].content_sha256 = $h' \
    "$TMP_LOCK" > "${TMP_LOCK}.new" && mv "${TMP_LOCK}.new" "$TMP_LOCK"

  echo "[SYNCED] $skill from $SYNC_SHA (content_sha256: ${content_hash:0:12}...)"
  UPDATED+=("$skill")
done

if [ "$DRY_RUN" -eq 0 ] && [ "${#UPDATED[@]}" -gt 0 ]; then
  jq --arg t "$NOW_ISO" '.last_synced_at = $t' "$TMP_LOCK" > "$LOCK_FILE"
fi

echo ""
echo "=== Summary ==="
echo "Source: $SOURCE_DIR @ $SYNC_SHA (branch: $SOURCE_BRANCH)"
echo "Updated: ${#UPDATED[@]} skills"
echo "Skipped: ${#SKIPPED[@]} skills (local modifications)"
echo "Missing: ${#MISSING[@]} skills (source file not found)"
[ "$DRY_RUN" -eq 1 ] && echo "(dry run -- no files written)"

# CLAUDE.md skills table check (advisory; never edits the file)
if [ "$NO_CLAUDE_MD_CHECK" -eq 0 ]; then
  CLAUDE_MD=""
  for candidate in "CLAUDE.md" ".claude/CLAUDE.md"; do
    if [ -f "$candidate" ] && grep -qE '\|\s*`/[a-zA-Z0-9_-]+`' "$candidate"; then
      CLAUDE_MD="$candidate"
      break
    fi
  done

  if [ -n "$CLAUDE_MD" ]; then
    UNDOCUMENTED=()
    for skill in $SKILL_NAMES; do
      # Match `/skill` as a whole token (backtick-wrapped) to avoid false matches
      if ! grep -qF "\`/$skill\`" "$CLAUDE_MD"; then
        UNDOCUMENTED+=("$skill")
      fi
    done

    if [ "${#UNDOCUMENTED[@]}" -gt 0 ]; then
      echo ""
      echo "=== CLAUDE.md skills table check ==="
      echo "Found skills table in $CLAUDE_MD, but these skills are NOT listed:"
      for skill in "${UNDOCUMENTED[@]}"; do
        # Extract description from YAML frontmatter of the pinned git content
        description=$(git -C "$SOURCE_DIR" show "$SYNC_SHA:.claude/skills/$skill/SKILL.md" 2>/dev/null | \
          awk '/^---$/{f++; next} f==1 && /^description:/{
            sub(/^description:[[:space:]]*/, "")
            sub(/^["'\'']/, "")
            sub(/["'\''][[:space:]]*$/, "")
            print
            exit
          }')
        if [ "${#description}" -gt 80 ]; then
          description="${description:0:77}..."
        fi
        echo "  - /$skill"
        echo "    Suggested row: | \`/$skill\` | <trigger> | $description |"
      done
      echo "Add these rows to $CLAUDE_MD manually (table schema varies; auto-edit is unsafe)."
    fi
  else
    echo "(CLAUDE.md skills table not found -- skipping documentation check)"
  fi
fi

[ "${#MISSING[@]}" -gt 0 ] && exit 2 || exit 0
